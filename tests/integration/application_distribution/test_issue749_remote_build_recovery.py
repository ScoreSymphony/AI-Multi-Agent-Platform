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
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from ai_multi_agent_platform.application_distribution.worker import (
    application_workspace_lifecycle,
)
from ai_multi_agent_platform.contracts import ExecutionHandle, ExecutionSnapshot, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DispatchState,
    DistributedRegistry,
    DistributedRuntime,
    MaterializingWorkerDispatcher,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerJobResult,
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


class _LostAcknowledgementDispatcher:
    """Accept one Worker job, then simulate loss of its dispatch acknowledgement."""

    def __init__(self, wrapped: ArtifactPublishingWorkerDispatcher) -> None:
        self._wrapped = wrapped
        self.dispatch_calls = 0

    @property
    def worker_id(self) -> str:
        return self._wrapped.worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        self.dispatch_calls += 1
        await self._wrapped.dispatch(job)
        raise RuntimeError("simulated application-build dispatch acknowledgement loss")

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._wrapped.get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._wrapped.cancel(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        return await self._wrapped.result(worker_job_id)


async def _recover_build(
    service: ApplicationDistributionService,
    release_id: str,
    *,
    target_id: str,
    key_prefix: str,
) -> ApplicationRelease:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5.0
    poll_index = 0
    last_status: BuildTargetStatus | None = None
    while loop.time() < deadline:
        release = await service.request_build(
            release_id,
            target_id=target_id,
            idempotency_key=f"{key_prefix}:poll:{poll_index}",
            actor_ref="user:tester",
        )
        poll_index += 1
        state = next(item for item in release.targets if item.target.target_id == target_id)
        last_status = state.status
        if state.status in {
            BuildTargetStatus.SUCCEEDED,
            BuildTargetStatus.FAILED,
            BuildTargetStatus.UNSUPPORTED,
        }:
            return release
        await asyncio.sleep(0.02)
    status = last_status.value if last_status is not None else "unknown"
    raise AssertionError(
        f"remote application build did not recover: {target_id}; last_status={status}"
    )


def test_lost_dispatch_ack_reconciles_same_application_build_without_duplicate_artifact(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        operation = OperationContext(
            correlation_id="issue-749-lost-ack",
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
                    display_name="issue-749-recovery-worker",
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

        transport = InProcessMessageTransport(provider_id="issue-749-recovery-workspace")
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
        canonical_dispatcher = ArtifactPublishingWorkerDispatcher(
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
        dispatcher = _LostAcknowledgementDispatcher(canonical_dispatcher)
        runtime.attach_worker(dispatcher)

        specification = BuildSpecification(
            command=(
                sys.executable,
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('dist').mkdir(); "
                    "Path('dist/app.bin').write_bytes(b'recovered-package')"
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
            application_id="recoverable-app",
            display_name="Recoverable App",
            version="1.0.0",
            channel=ReleaseChannel.STABLE,
            visibility=ReleaseVisibility.PRIVATE,
            project_id=project_id,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            source_revision="source-revision-749-recovery",
            build_specification=specification,
            creator_ref="user:tester",
        )

        try:
            with pytest.raises(
                RuntimeError,
                match="simulated application-build dispatch acknowledgement loss",
            ):
                await service.request_build(
                    release.release_id,
                    target_id="linux-x64",
                    idempotency_key="issue-749-lost-ack:initial",
                    actor_ref="user:tester",
                )

            after_loss = await releases.get(release.release_id)
            target_after_loss = after_loss.targets[0]
            assert target_after_loss.task_id is not None
            assert target_after_loss.run_id is not None
            canonical_task_id = target_after_loss.task_id
            canonical_run_id = target_after_loss.run_id

            assert dispatcher.dispatch_calls == 1
            assert len(runtime.records()) == 1
            lost_record = runtime.records()[0]
            assert lost_record.state is DispatchState.LOST
            assert lost_record.worker_id == worker_id
            assert lost_record.job.execution.run_id == canonical_run_id
            assert lost_record.last_error == "dispatch_outcome_unknown"

            recovered = await _recover_build(
                service,
                release.release_id,
                target_id="linux-x64",
                key_prefix="issue-749-lost-ack:recover",
            )

            recovered_target = recovered.targets[0]
            assert recovered.status is ReleaseStatus.READY
            assert recovered_target.status is BuildTargetStatus.SUCCEEDED
            assert recovered_target.task_id == canonical_task_id
            assert recovered_target.run_id == canonical_run_id
            assert dispatcher.dispatch_calls == 1
            assert len(runtime.records()) == 1
            assert runtime.records()[0].state is DispatchState.TERMINAL

            assert len(recovered.artifacts) == 1
            artifact = recovered.artifacts[0]
            assert artifact.target_id == "linux-x64"
            run = await kernel.get_run(canonical_task_id, canonical_run_id)
            assert run.status is RunStatus.SUCCEEDED
            assert run.artifact_ids == (artifact.artifact_id,)

            file_context = DataAccessContext(
                operation=operation,
                actor_ref="user:tester",
                task_id=canonical_task_id,
                run_id=canonical_run_id,
            )
            canonical_file = await files.get_file(artifact.file_id, file_context)
            assert canonical_file.sha256 == artifact.sha256
            assert artifact.artifact_id in canonical_file.artifact_ids
            assert await files.verify_checksum(canonical_file.file_id, file_context)

            repeated = await service.request_build(
                release.release_id,
                target_id="linux-x64",
                idempotency_key="issue-749-lost-ack:recover:poll:0",
                actor_ref="user:tester",
            )
            assert repeated.artifacts == recovered.artifacts
            assert dispatcher.dispatch_calls == 1
            assert len(runtime.records()) == 1
        finally:
            workspace_endpoint.cancel()
            with suppress(asyncio.CancelledError):
                await workspace_endpoint
            await transport.close(graceful=False)

    asyncio.run(scenario())
