from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ExecutionRequest,
    ExecutionStatus,
    OperationContext,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import WorkerJobRequest
from ai_multi_agent_platform.distributed.workspace import (
    MaterializingWorkerDispatcher,
    WorkspaceJobMaterializationResolver,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
    WorkspaceBoundLocalWorker,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    WorkspaceAccessMode,
    WorkspaceChangeKind,
    WorkspaceFile,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-449",
            owner_type="service",
            owner_id="issue-449",
            project_id=project_id,
        ),
        actor_ref="service:issue-449",
    )


def _execution_request(project_id: str) -> ExecutionRequest:
    task_id = new_id("task")
    return ExecutionRequest(
        run_id=new_id("run"),
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id=f"issue-449:{task_id}",
            project_id=project_id,
        ),
    )


async def _canonical_workspace(tmp_path: Path, *, content: bytes = b"canonical workspace"):
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
    record = await files.create_file(content, context, content_type="text/plain")
    workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="service", id="issue-449"),
        workspace_type=WorkspaceType.REMOTE,
        context=context,
        access_mode=WorkspaceAccessMode.READ_WRITE,
        files=(
            WorkspaceFile(
                relative_path="src/input.txt",
                file_id=record.file_id,
                sha256=record.sha256,
            ),
        ),
    )
    assert workspace.base_snapshot_id is not None
    snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
    return workspaces, files, context, workspace, snapshot


async def _read_file(
    files: LocalFileProvider,
    file_id: str,
    context: DataAccessContext,
) -> bytes:
    return b"".join([chunk async for chunk in files.stream_file(file_id, context)])


def test_materializing_dispatcher_uses_concrete_remote_materializer_for_full_lifecycle(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot = await _canonical_workspace(
            tmp_path,
            content=b"before remote execution",
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = InProcessMessageTransport(provider_id="issue-449-lifecycle")
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: context,
        )
        materialized_file = worker_root / workspace.id / snapshot.id / "src" / "input.txt"
        execution_token = f"{workspace.id}/{snapshot.id}"
        lifecycle_by_token: dict[str, FakeLifecycleBackend] = {}

        def lifecycle_factory(token: str) -> FakeLifecycleBackend:
            assert token == execution_token
            assert materialized_file.read_bytes() == b"before remote execution"
            lifecycle = FakeLifecycleBackend()
            lifecycle_by_token[token] = lifecycle
            return lifecycle

        worker = WorkspaceBoundLocalWorker(worker_id, store, lifecycle_factory)
        dispatcher = MaterializingWorkerDispatcher(
            worker,
            materializer,
            WorkspaceJobMaterializationResolver(workspaces),
        )
        job = WorkerJobRequest(
            execution=_execution_request(workspace.project_id),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
        )

        try:
            handle = await dispatcher.dispatch(job)
            assert lifecycle_by_token.keys() == {execution_token}
            materialized_file.write_bytes(b"changed by remote execution")
            lifecycle_by_token[execution_token].complete(
                handle.run_id,
                status=ExecutionStatus.SUCCEEDED,
            )

            result = await dispatcher.result(job.worker_job_id)
            assert result is not None
            assert result.status.value == "succeeded"

            evidence = dispatcher.evidence(job.worker_job_id)
            assert evidence.receipt is not None
            assert evidence.receipt.workspace_id == workspace.id
            assert evidence.receipt.snapshot_id == snapshot.id
            assert evidence.receipt.worker_ref == worker_id
            assert evidence.result is not None
            assert len(evidence.result.changes) == 1
            change = evidence.result.changes[0]
            assert change.relative_path == "src/input.txt"
            assert change.kind is WorkspaceChangeKind.MODIFIED
            assert change.file_id is not None
            assert await _read_file(files, change.file_id, context) == b"changed by remote execution"
            assert evidence.cleanup is not None
            assert evidence.cleanup.succeeded is True
            assert evidence.cleanup.outcome is MaterializationOutcome.SUCCEEDED
            assert not (worker_root / workspace.id / snapshot.id).exists()
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_remote_cleanup_filesystem_failure_returns_failed_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot = await _canonical_workspace(tmp_path)
        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = InProcessMessageTransport(provider_id="issue-449-cleanup-failure")
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: context,
        )
        request = await WorkspaceJobMaterializationResolver(workspaces).resolve(
            WorkerJobRequest(
                execution=_execution_request(workspace.project_id),
                workspace_ref=workspace.id,
                snapshot_ref=snapshot.id,
            )
        )
        assert request is not None

        try:
            receipt = await materializer.materialize(request)
            assert (worker_root / workspace.id / snapshot.id).exists()

            def fail_remove(_root: Path) -> None:
                raise OSError("simulated cleanup filesystem failure")

            monkeypatch.setattr(
                WorkerWorkspaceMaterializationStore,
                "_remove_materialization",
                staticmethod(fail_remove),
            )
            acknowledgement = await materializer.cleanup(
                receipt,
                MaterializationOutcome.SUCCEEDED,
            )

            assert acknowledgement.succeeded is False
            assert acknowledgement.error_code == "workspace_cleanup_failed"
            assert acknowledgement.outcome is MaterializationOutcome.SUCCEEDED
            assert acknowledgement.workspace_id == workspace.id
            assert acknowledgement.snapshot_id == snapshot.id
            assert acknowledgement.materialization_ref == receipt.materialization_ref
            assert (worker_root / workspace.id / snapshot.id).exists()
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
