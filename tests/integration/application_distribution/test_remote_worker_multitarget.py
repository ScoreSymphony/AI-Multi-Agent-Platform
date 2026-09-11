from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

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
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.application_distribution.worker import application_workspace_lifecycle
from ai_multi_agent_platform.contracts import OperationContext
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
    poll_index = 0
    last_status: BuildTargetStatus | None = None
    while loop.time() < deadline:
        release = await service.request_build(
            release_id,
            target_id=target_id,
            idempotency_key=f"{idempotency_key}:poll:{poll_index}",
            actor_ref="user:tester",
        )
        poll_index += 1
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
    raise AssertionError(f"remote application build did not complete: {target_id}; {status=}")


def test_multi_target_release_uses_distinct_eligible_workers_and_preserves_release_identity(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="issue-749-multitarget",
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
        linux_node_id = new_id("node")
        linux_worker_id = new_id("worker")
        windows_node_id = new_id("node")
        windows_worker_id = new_id("worker")
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(
                    node_id=linux_node_id,
                    display_name="issue-749-linux-builder",
                    os_name="linux",
                    architecture="x86_64",
                ),
                workers=(
                    WorkerRecord(
                        worker_id=linux_worker_id,
                        node_id=linux_node_id,
                        capability_refs=(APPLICATION_BUILD_ACTION,),
                    ),
                ),
                service_identity_ref=linux_worker_id,
            )
        )
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(
                    node_id=windows_node_id,
                    display_name="issue-749-windows-builder",
                    os_name="windows",
                    architecture="x86_64",
                ),
                workers=(
                    WorkerRecord(
                        worker_id=windows_worker_id,
                        node_id=windows_node_id,
                        capability_refs=(APPLICATION_BUILD_ACTION,),
                    ),
                ),
                service_identity_ref=windows_worker_id,
            )
        )

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

        transport = InProcessMessageTransport(provider_id="issue-749-multitarget-workspace")
        endpoint_tasks: list[asyncio.Task[None]] = []
        for worker_id, worker_root in (
            (linux_worker_id, tmp_path / "remote-linux"),
            (windows_worker_id, tmp_path / "remote-windows"),
        ):
            store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
            endpoint_tasks.append(
                asyncio.create_task(WorkerWorkspaceTransportEndpoint(store, transport).serve())
            )
            reference_executor = ReferenceExecutor(worker_root)
            worker = WorkspaceBoundLocalWorker(
                worker_id,
                store,
                lambda execution_workspace, root=worker_root, executor=reference_executor: (
                    application_workspace_lifecycle(root, executor, execution_workspace)
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
        release = await service.create_release(
            application_id="multi-target-app",
            display_name="Multi Target App",
            version="1.4.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-749-multitarget",
            build_specification=specification,
            creator_ref="user:tester",
        )
        canonical_identity = (
            release.release_id,
            release.application_id,
            release.version,
            release.source_revision,
            release.workspace_id,
            release.workspace_snapshot_id,
            release.workspace_content_checksum,
        )

        try:
            linux_built = await _finish_build(
                service,
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-linux",
            )
            assert linux_built.release_id == release.release_id
            assert linux_built.status is ReleaseStatus.PARTIAL

            windows_built = await _finish_build(
                service,
                release.release_id,
                target_id="windows-x64",
                idempotency_key="issue-749-windows",
            )
            assert windows_built.release_id == release.release_id
            assert windows_built.status is ReleaseStatus.PARTIAL

            unsupported = await service.request_build(
                release.release_id,
                target_id="macos-arm64",
                idempotency_key="issue-749-macos",
                actor_ref="user:tester",
            )
            assert unsupported.release_id == release.release_id
            assert unsupported.status is ReleaseStatus.PARTIAL
            assert (
                unsupported.release_id,
                unsupported.application_id,
                unsupported.version,
                unsupported.source_revision,
                unsupported.workspace_id,
                unsupported.workspace_snapshot_id,
                unsupported.workspace_content_checksum,
            ) == canonical_identity

            states = {item.target.target_id: item for item in unsupported.targets}
            assert states["linux-x64"].status is BuildTargetStatus.SUCCEEDED
            assert states["windows-x64"].status is BuildTargetStatus.SUCCEEDED
            assert states["macos-arm64"].status is BuildTargetStatus.UNSUPPORTED
            assert states["macos-arm64"].task_id is None
            assert states["macos-arm64"].run_id is None
            assert "no eligible execution host" in (states["macos-arm64"].failure_reason or "")

            artifacts = {artifact.target_id: artifact for artifact in unsupported.artifacts}
            assert set(artifacts) == {"linux-x64", "windows-x64"}
            assert artifacts["linux-x64"].filename == "linux.bin"
            assert artifacts["windows-x64"].filename == "windows.bin"

            records = {record.job.execution.run_id: record for record in runtime.records()}
            linux_run_id = states["linux-x64"].run_id
            windows_run_id = states["windows-x64"].run_id
            assert linux_run_id is not None
            assert windows_run_id is not None
            assert records[linux_run_id].worker_id == linux_worker_id
            assert records[windows_run_id].worker_id == windows_worker_id
            assert records[linux_run_id].job.requirements.os_name == "linux"
            assert records[windows_run_id].job.requirements.os_name == "windows"
            assert len(records) == 2
        finally:
            for endpoint in endpoint_tasks:
                endpoint.cancel()
            for endpoint in endpoint_tasks:
                with suppress(asyncio.CancelledError):
                    await endpoint
            await transport.close(graceful=False)

    asyncio.run(scenario())
