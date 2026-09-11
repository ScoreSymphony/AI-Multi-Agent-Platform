from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    DistributedApplicationBuildLifecycleBackend,
    DistributedBuildTargetMatcher,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseVisibility,
)
from ai_multi_agent_platform.configuration import SecretReference
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    LocalWorker,
    NodeRecord,
    RegistrationRequest,
    WorkerRecord,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.testing import FakeAuthorizationProvider, FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


async def _remote_build_fixture(
    tmp_path: Path,
    *,
    secret_environment: dict[str, SecretReference] | None = None,
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[
    ApplicationDistributionService,
    ApplicationRelease,
    DistributedRuntime,
    FakeLifecycleBackend,
    str,
]:
    project_id = new_id("project")
    operation = OperationContext(
        correlation_id="issue-749-security",
        owner_type="user",
        owner_id="tester",
        project_id=project_id,
    )
    data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="user", id="tester"),
        workspace_type=WorkspaceType.PERSISTENT_PROJECT,
        context=data_context,
    )
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

    releases = InMemoryApplicationReleaseRepository()
    bindings = InMemoryRunWorkspaceBindingRepository()
    registry = DistributedRegistry()
    runtime = DistributedRuntime(registry, authorization=authorization)
    node_id = new_id("node")
    worker_id = new_id("worker")
    runtime.register(
        RegistrationRequest(
            node=NodeRecord(
                node_id=node_id,
                display_name="issue-749-secure-worker",
                os_name="linux",
                architecture="x86_64",
            ),
            workers=(
                WorkerRecord(
                    worker_id=worker_id,
                    node_id=node_id,
                    capability_refs=(APPLICATION_BUILD_ACTION,),
                    trust_level="trusted",
                ),
            ),
            service_identity_ref=worker_id,
        )
    )
    worker_lifecycle = FakeLifecycleBackend()
    runtime.attach_worker(LocalWorker(worker_id, worker_lifecycle))

    lifecycle = DistributedApplicationBuildLifecycleBackend(
        releases,
        files,
        bindings,
        runtime,
    )
    kernel = PlatformKernel(
        orchestrator=ReferenceOrchestrator(),
        lifecycle=lifecycle,
        repository=InMemoryKernelRepository(),
    )
    service = ApplicationDistributionService(
        releases,
        kernel=kernel,
        files=files,
        workspaces=workspaces,
        run_workspace_bindings=bindings,
        target_matcher=DistributedBuildTargetMatcher(
            registry,
            scheduler=runtime.scheduler,
        ),
    )
    target = BuildTarget(
        target_id="linux-x64",
        os_name="linux",
        architecture="x86_64",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )
    specification = BuildSpecification(
        command=("tool", "build"),
        targets=(target,),
        secret_environment=secret_environment or {},
    )
    release = await service.create_release(
        application_id="secure-remote-app",
        display_name="Secure Remote App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=workspace.id,
        workspace_snapshot_id=snapshot.id,
        source_revision="source-revision-749-security",
        build_specification=specification,
        creator_ref="user:tester",
    )
    return service, release, runtime, worker_lifecycle, worker_id


def test_secret_backed_remote_build_fails_before_worker_dispatch_until_scoped_delivery_exists(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        reference = SecretReference(
            provider="local-secrets",
            secret_id="application-build-token",
            scope=project_id,
        )
        service, release, runtime, worker_lifecycle, _ = await _remote_build_fixture(
            tmp_path,
            secret_environment={"PRIVATE_INDEX_TOKEN": reference},
        )

        with pytest.raises(ContractError) as exc_info:
            await service.request_build(
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-secret-remote",
                actor_ref="user:tester",
            )

        assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
        assert "scoped Worker secret delivery" in exc_info.value.message
        assert runtime.records() == ()
        assert worker_lifecycle.start_calls == []
        assert "application-build-token" not in repr(runtime.records())

    asyncio.run(scenario())


def test_application_remote_build_honors_canonical_worker_authorization_denial(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        authorization = FakeAuthorizationProvider(allowed=False)
        service, release, runtime, worker_lifecycle, worker_id = await _remote_build_fixture(
            tmp_path,
            authorization=authorization,
        )

        with pytest.raises(ContractError) as exc_info:
            await service.request_build(
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-worker-authz-denied",
                actor_ref="user:tester",
            )

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert runtime.records() == ()
        assert runtime.registry.active_reservations() == ()
        assert worker_lifecycle.start_calls == []
        assert len(authorization.calls) == 1
        request = authorization.calls[0]
        assert request.action == "execute"
        assert request.resource_ref == worker_id
        assert request.resource_type == "worker"
        assert request.side_effect == "worker.dispatch"
        assert request.run_id is not None
        assert request.workspace_id == release.workspace_id

    asyncio.run(scenario())
