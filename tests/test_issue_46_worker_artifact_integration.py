from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from ai_multi_agent_platform.contracts import ExecutionRequest, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import (
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    ExecutorWorker,
    MaterializingWorkerDispatcher,
    WorkerJobRequest,
    WorkspaceJobMaterializationResolver,
    executor_worker_input,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.testing import FakeOrchestrator
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import WorkspaceAccessMode, WorkspaceChangeKind, WorkspaceType
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def test_remote_worker_file_becomes_canonical_run_artifact(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
        )
        task = await kernel.create_task(
            idempotency_key="issue-46-worker-artifact:create",
            title="Worker artifact promotion",
            objective="Return Worker workspace output as canonical Artifact evidence",
            owner_type="user",
            owner_id="issue-46-owner",
            project_id=project_id,
        )
        await kernel.ready_task(
            idempotency_key="issue-46-worker-artifact:ready",
            task_id=task.task_id,
        )
        run = await kernel.create_run(
            idempotency_key="issue-46-worker-artifact:run",
            task_id=task.task_id,
        )
        operation = OperationContext(
            correlation_id=task.task_id,
            owner_type="user",
            owner_id="issue-46-owner",
            project_id=project_id,
            causation_id="issue-46-worker-artifact:test",
        )
        data_context = DataAccessContext(
            operation=operation,
            actor_ref="user:issue-46-owner",
            task_id=task.task_id,
            run_id=run.run_id,
        )
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="issue-46-owner"),
            workspace_type=WorkspaceType.REMOTE,
            context=data_context,
            access_mode=WorkspaceAccessMode.READ_WRITE,
        )
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)

        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = InProcessMessageTransport(provider_id="issue-46-worker-artifact")
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: data_context,
        )

        def execution_workspace(job: WorkerJobRequest) -> str:
            assert job.workspace_ref is not None
            assert job.snapshot_ref is not None
            return store.execution_workspace(job.workspace_ref, job.snapshot_ref)

        worker = ExecutorWorker(
            worker_id,
            ReferenceExecutor(worker_root),
            workspace="unused",
            workspace_resolver=execution_workspace,
        )
        materializing = MaterializingWorkerDispatcher(
            worker,
            materializer,
            WorkspaceJobMaterializationResolver(workspaces),
        )
        dispatcher = ArtifactPublishingWorkerDispatcher(
            materializing,
            CanonicalWorkspaceArtifactPublisher(
                workspaces,
                files,
                kernel,
                lambda _workspace: data_context,
            ),
        )
        job = WorkerJobRequest(
            execution=ExecutionRequest(
                run_id=run.run_id,
                subject_type="task",
                subject_id=task.task_id,
                context=operation,
                input=executor_worker_input(
                    action="write_artifact",
                    arguments={
                        "path": "out/worker-evidence.txt",
                        "content": "canonical Worker evidence",
                    },
                ),
            ),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
            actor_ref="user:issue-46-owner",
        )

        try:
            await dispatcher.dispatch(job)
            result = await dispatcher.result(job.worker_job_id)
            assert result is not None
            assert len(result.artifact_refs) == 1
            artifact_id = result.artifact_refs[0]
            assert validate_id(artifact_id, "artifact") == artifact_id

            task_state = await kernel.get_task(task.task_id)
            run_state = await kernel.get_run(task.task_id, run.run_id)
            assert artifact_id in task_state.artifact_ids
            assert artifact_id in run_state.artifact_ids

            evidence = dispatcher.evidence(job.worker_job_id)
            assert evidence.result is not None
            assert evidence.result.artifact_ids == (artifact_id,)
            assert len(evidence.result.changes) == 1
            change = evidence.result.changes[0]
            assert change.kind is WorkspaceChangeKind.CREATED
            assert change.relative_path == "out/worker-evidence.txt"
            assert change.file_id is not None
            assert change.sha256 is not None

            linked = await files.get_file(change.file_id, data_context)
            assert linked.artifact_ids == (artifact_id,)
            assert await files.verify_checksum(change.file_id, data_context)
            content = b"".join(
                [chunk async for chunk in files.stream_file(change.file_id, data_context)]
            )
            assert content == b"canonical Worker evidence"

            repeated = await dispatcher.result(job.worker_job_id)
            assert repeated is not None
            assert repeated.artifact_refs == (artifact_id,)
            repeated_task = await kernel.get_task(task.task_id)
            repeated_run = await kernel.get_run(task.task_id, run.run_id)
            assert repeated_task.artifact_ids.count(artifact_id) == 1
            assert repeated_run.artifact_ids.count(artifact_id) == 1
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
