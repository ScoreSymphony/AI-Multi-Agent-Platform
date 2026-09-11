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
from ai_multi_agent_platform.application_distribution.worker import (
    application_workspace_lifecycle,
)
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
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, new_id
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
    deadline = loop.time() + 30.0
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


def test_application_build_dispatches_to_canonical_remote_worker_and_returns_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="issue-749-remote-build",
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
        node_id = new_id("node")
        worker_id = new_id("worker")
        runtime.register(
            RegistrationRequest(
                node=NodeRecord(
                    node_id=node_id,
                    display_name="issue-749-linux-builder",
                    os_name="linux",
                    architecture="x86_64",
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

        transport = InProcessMessageTransport(provider_id="issue-749-workspace")
        worker_root = tmp_path / "remote-worker"
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        workspace_endpoint = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
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
        dispatcher = ArtifactPublishingWorkerDispatcher(
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
        runtime.attach_worker(dispatcher)

        specification = BuildSpecification(
            command=(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('dist').mkdir(); "
                    "Path('dist/app.bin').write_bytes(b'remote-package')"
                ),
            ),
            targets=(
                BuildTarget(
                    target_id="linux-x64",
                    os_name="linux",
                    architecture="x86_64",
                    package_type=PackageType.ARCHIVE,
                    output_path="dist/app.bin",
                ),
            ),
        )
        release = await service.create_release(
            application_id="remote-app",
            display_name="Remote App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-749",
            build_specification=specification,
            creator_ref="user:tester",
        )

        try:
            built = await _finish_build(
                service,
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-remote-build",
            )

            assert built.status is ReleaseStatus.READY
            assert built.targets[0].status is BuildTargetStatus.SUCCEEDED
            assert len(built.artifacts) == 1
            artifact = built.artifacts[0]
            assert artifact.filename == "app.bin"
            assert built.targets[0].task_id is not None
            assert built.targets[0].run_id is not None
            run = await kernel.get_run(
                built.targets[0].task_id,
                built.targets[0].run_id,
            )
            assert run.status is RunStatus.SUCCEEDED
            assert artifact.artifact_id in run.artifact_ids

            record = next(
                item
                for item in runtime.records()
                if item.job.execution.run_id == built.targets[0].run_id
            )
            assert record.worker_id == worker_id
            assert record.job.workspace_ref == workspace.id
            assert record.job.snapshot_ref == snapshot.id
            assert record.job.requirements.os_name == "linux"
            assert record.job.requirements.architecture == "x86_64"
            assert APPLICATION_BUILD_ACTION in record.job.requirements.capability_refs
            assert str(worker_root) not in repr(record.job.execution.input)
            assert str(tmp_path / "control-workspaces") not in repr(record.job.execution.input)

            file_context = DataAccessContext(
                operation=operation,
                actor_ref="user:tester",
                task_id=built.targets[0].task_id,
                run_id=built.targets[0].run_id,
            )
            canonical = await files.get_file(artifact.file_id, file_context)
            assert canonical.sha256 == artifact.sha256
            assert canonical.metadata["workspace_id"] == workspace.id
            assert canonical.metadata["workspace_snapshot_id"] == snapshot.id
            assert canonical.metadata["relative_path"] == "dist/app.bin"
            assert artifact.artifact_id in canonical.artifact_ids
            assert await files.verify_checksum(canonical.file_id, file_context)
            assert (
                b"".join(
                    [chunk async for chunk in files.stream_file(canonical.file_id, file_context)]
                )
                == b"remote-package"
            )

            repeated = await service.request_build(
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-remote-build",
                actor_ref="user:tester",
            )
            assert repeated.artifacts == built.artifacts
            assert len(runtime.records()) == 1
        finally:
            workspace_endpoint.cancel()
            with suppress(asyncio.CancelledError):
                await workspace_endpoint
            await transport.close(graceful=False)

    asyncio.run(scenario())
