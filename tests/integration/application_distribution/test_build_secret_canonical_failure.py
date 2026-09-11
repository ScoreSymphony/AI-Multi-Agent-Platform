from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationBuildLifecycleBackend,
    ApplicationDistributionService,
    BuildSpecification,
    BuildTarget,
    BuildTargetStatus,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
from ai_multi_agent_platform.execution import (
    ExecutionRequest,
    ExecutionResult,
    Executor,
    ExecutorDescriptor,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


class _FailIfExecuted(Executor):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def descriptor(self) -> ExecutorDescriptor:
        return ExecutorDescriptor(
            executor_id="must-not-run",
            capabilities=(APPLICATION_BUILD_ACTION,),
        )

    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        del request
        self.calls += 1
        raise AssertionError("build command must not execute when secret resolution fails")


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


def test_missing_build_secret_becomes_canonical_failed_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="application-build-missing-secret",
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
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id or "")
        missing_reference = SecretReference(
            provider="local-secrets",
            secret_id="missing-build-secret",
            scope=project_id,
        )
        secrets = LocalSecretProvider()
        releases = InMemoryApplicationReleaseRepository()
        bindings = InMemoryRunWorkspaceBindingRepository()
        executor = _FailIfExecuted()
        lifecycle = ApplicationBuildLifecycleBackend(
            releases,
            workspaces,
            files,
            bindings,
            executor,
            secret_provider=secrets,
            secret_consumer_ref="service:application-build-secrets",
            secret_purpose="application_build",
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
        )
        release = await service.create_release(
            application_id="missing-secret-app",
            display_name="Missing Secret App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-missing-secret",
            build_specification=BuildSpecification(
                command=("must-not-run",),
                targets=(_target(),),
                secret_environment={"PRIVATE_INDEX_TOKEN": missing_reference},
            ),
            creator_ref="user:tester",
        )

        failed = await service.request_build(
            release.release_id,
            target_id="local-test",
            idempotency_key="missing-secret-build",
            actor_ref="user:tester",
        )

        assert executor.calls == 0
        assert failed.status is ReleaseStatus.FAILED
        target = failed.targets[0]
        assert target.status is BuildTargetStatus.FAILED
        assert target.task_id is not None
        assert target.run_id is not None

        run = await kernel.get_run(target.task_id, target.run_id)
        assert run.status is RunStatus.FAILED
        assert run.output["output"] == {
            "contract_error": {
                "code": "not_found",
                "retryable": False,
            }
        }
        serialized = repr(run.output)
        assert "missing-build-secret" not in serialized
        assert "PRIVATE_INDEX_TOKEN" not in serialized
        assert "secret reference was not found" in serialized

    asyncio.run(scenario())
