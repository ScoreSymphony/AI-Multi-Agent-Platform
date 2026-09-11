from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    APPLICATION_BUILD_ACTION,
    ApplicationDistributionService,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetStatus,
    DistributedApplicationBuildLifecycleBackend,
    DistributedBuildTargetMatcher,
    InMemoryApplicationReleaseRepository,
    PackageType,
    PublishContext,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.application_distribution.worker import (
    application_workspace_lifecycle,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DistributedRegistry,
    DistributedRuntime,
    MaterializingWorkerDispatcher,
    NodeRecord,
    RegistrationRequest,
    WorkerRecord,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
    WorkspaceBoundLocalWorker,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.orchestration import ReferenceOrchestrator
from ai_multi_agent_platform.security import ActorIdentity, ActorType
from ai_multi_agent_platform.workspaces import (
    InMemoryRunWorkspaceBindingRepository,
    LocalWorkspaceProvider,
    WorkspaceType,
)


async def _finish_build(
    service: ApplicationDistributionService,
    release_id: str,
    *,
    target_id: str,
    idempotency_key: str,
) -> ApplicationRelease:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    last_status: BuildTargetStatus | None = None
    while loop.time() < deadline:
        release = await service.request_build(
            release_id,
            target_id=target_id,
            idempotency_key=idempotency_key,
            actor_ref="user:tester",
        )
        target = next(item for item in release.targets if item.target.target_id == target_id)
        last_status = target.status
        if target.status in {
            BuildTargetStatus.SUCCEEDED,
            BuildTargetStatus.FAILED,
            BuildTargetStatus.UNSUPPORTED,
        }:
            return release
        await asyncio.sleep(0.02)
    status = last_status.value if last_status is not None else "unknown"
    raise AssertionError(
        f"remote application build did not complete: {target_id}; last_status={status}"
    )


def test_multi_target_release_routes_to_distinct_workers_and_keeps_unsupported_explicit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="issue-749-multi-target",
            owner_type="user",
            owner_id="tester",
            project_id=project_id,
        )
        data_context = DataAccessContext(operation=operation, actor_ref="user:tester")
        files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
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
        runtime = DistributedRuntime(registry)
        transport = InProcessMessageTransport(provider_id="issue-749-multi-target-workspace")
        endpoint_tasks: list[asyncio.Task[None]] = []

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

        def attach_worker(os_name: str, architecture: str) -> tuple[str, str]:
            node_id = new_id("node")
            worker_id = new_id("worker")
            runtime.register(
                RegistrationRequest(
                    node=NodeRecord(
                        node_id=node_id,
                        display_name=f"issue-749-{os_name}-{architecture}",
                        os_name=os_name,
                        architecture=architecture,
                    ),
                    workers=(
                        WorkerRecord(
                            worker_id=worker_id,
                            node_id=node_id,
                            capability_refs=(APPLICATION_BUILD_ACTION,),
                        ),
                    ),
                    service_identity_ref=worker_id,
                )
            )
            worker_root = tmp_path / f"worker-{worker_id}"
            store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
            endpoint_tasks.append(
                asyncio.create_task(WorkerWorkspaceTransportEndpoint(store, transport).serve())
            )
            reference_executor = ReferenceExecutor(worker_root)
            worker = WorkspaceBoundLocalWorker(
                worker_id,
                store,
                lambda execution_workspace: application_workspace_lifecycle(
                    worker_root,
                    reference_executor,
                    execution_workspace,
                ),
            )
            materializer = TransportRemoteWorkspaceMaterializer(
                worker_id,
                transport,
                workspaces,
                files,
                lambda _workspace: data_context,
            )
            runtime.attach_worker(
                ArtifactPublishingWorkerDispatcher(
                    MaterializingWorkerDispatcher(
                        worker,
                        materializer,
                        WorkspaceJobMaterializationResolver(workspaces),
                    ),
                    CanonicalWorkspaceArtifactPublisher(
                        workspaces,
                        files,
                        kernel,
                        lambda _workspace: data_context,
                    ),
                )
            )
            return node_id, worker_id

        linux_node, linux_worker = attach_worker("linux", "x86_64")
        windows_node, windows_worker = attach_worker("windows", "x86_64")

        specification = BuildSpecification(
            command=(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('dist').mkdir(exist_ok=True); "
                    "Path('dist/linux.bin').write_bytes(b'linux-package'); "
                    "Path('dist/windows.bin').write_bytes(b'windows-package')"
                ),
            ),
            targets=(
                BuildTarget(
                    target_id="linux-x64",
                    os_name="linux",
                    architecture="x86_64",
                    package_type=PackageType.ARCHIVE,
                    output_path="dist/linux.bin",
                ),
                BuildTarget(
                    target_id="windows-x64",
                    os_name="windows",
                    architecture="x86_64",
                    package_type=PackageType.ARCHIVE,
                    output_path="dist/windows.bin",
                ),
                BuildTarget(
                    target_id="macos-arm64",
                    os_name="macos",
                    architecture="arm64",
                    package_type=PackageType.ARCHIVE,
                    output_path="dist/macos.bin",
                ),
            ),
        )
        created = await service.create_release(
            application_id="multi-target-app",
            display_name="Multi Target App",
            version="1.4.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-749-multi",
            build_specification=specification,
            creator_ref="user:tester",
        )
        canonical_identity = (
            created.release_id,
            created.application_id,
            created.version,
            created.source_revision,
            created.workspace_id,
            created.workspace_snapshot_id,
            created.workspace_content_checksum,
        )

        try:
            after_linux = await _finish_build(
                service,
                created.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-linux",
            )
            assert after_linux.status is ReleaseStatus.PARTIAL

            after_windows = await _finish_build(
                service,
                created.release_id,
                target_id="windows-x64",
                idempotency_key="issue-749-windows",
            )
            assert after_windows.status is ReleaseStatus.PARTIAL

            final = await service.request_build(
                created.release_id,
                target_id="macos-arm64",
                idempotency_key="issue-749-macos",
                actor_ref="user:tester",
            )

            assert (
                final.release_id,
                final.application_id,
                final.version,
                final.source_revision,
                final.workspace_id,
                final.workspace_snapshot_id,
                final.workspace_content_checksum,
            ) == canonical_identity
            assert final.status is ReleaseStatus.PARTIAL

            states = {item.target.target_id: item for item in final.targets}
            assert states["linux-x64"].status is BuildTargetStatus.SUCCEEDED
            assert states["windows-x64"].status is BuildTargetStatus.SUCCEEDED
            assert states["macos-arm64"].status is BuildTargetStatus.UNSUPPORTED
            assert states["macos-arm64"].task_id is None
            assert states["macos-arm64"].run_id is None
            assert states["macos-arm64"].failure_reason == (
                "no eligible execution host for build target requirements"
            )

            assert {artifact.target_id for artifact in final.artifacts} == {
                "linux-x64",
                "windows-x64",
            }
            assert {artifact.filename for artifact in final.artifacts} == {
                "linux.bin",
                "windows.bin",
            }

            records_by_run = {record.job.execution.run_id: record for record in runtime.records()}
            linux_run = states["linux-x64"].run_id
            windows_run = states["windows-x64"].run_id
            assert linux_run is not None
            assert windows_run is not None
            assert records_by_run[linux_run].worker_id == linux_worker
            assert records_by_run[windows_run].worker_id == windows_worker
            assert records_by_run[linux_run].worker_id != records_by_run[windows_run].worker_id
            assert registry.get_worker(linux_worker).node_id == linux_node
            assert registry.get_worker(windows_worker).node_id == windows_node
            assert len(runtime.records()) == 2

            with pytest.raises(ContractError, match="only a fully built release") as exc_info:
                await service.preview_publication(
                    final.release_id,
                    publisher_id="unused-because-release-is-not-ready",
                    context=PublishContext(
                        actor=ActorIdentity("user:tester", ActorType.HUMAN),
                        operation=operation,
                    ),
                )
            assert exc_info.value.code is ErrorCode.CONFLICT
        finally:
            for endpoint_task in endpoint_tasks:
                endpoint_task.cancel()
            for endpoint_task in endpoint_tasks:
                with suppress(asyncio.CancelledError):
                    await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
