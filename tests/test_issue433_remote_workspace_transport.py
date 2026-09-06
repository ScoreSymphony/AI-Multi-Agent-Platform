from __future__ import annotations

import asyncio
import multiprocessing
from contextlib import suppress
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.distributed import WorkerJobRequest
from ai_multi_agent_platform.distributed.workspace import WorkspaceJobMaterializationResolver
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
    WorkspaceBoundLocalWorker,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.messaging import (
    InProcessMessageTransport,
    PublishReceipt,
    TcpMessageBroker,
    TcpMessageTransport,
    TransportEnvelope,
)
from ai_multi_agent_platform.testing.fakes import FakeLifecycleBackend
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    WorkspaceAccessMode,
    WorkspaceChangeKind,
    WorkspaceFile,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces.reference import LocalWorkspaceProvider

TEST_TRANSPORT_KEY = "issue-433-test-transport-key"


class _RecordingTransport(InProcessMessageTransport):
    def __init__(self) -> None:
        super().__init__(provider_id="issue-433-recording")
        self.published: list[tuple[str, TransportEnvelope]] = []

    async def _publish_once(
        self,
        topic: str,
        envelope: TransportEnvelope,
    ) -> PublishReceipt:
        self.published.append((topic, envelope))
        return await super()._publish_once(topic, envelope)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-433",
            owner_type="service",
            owner_id="issue-433",
            project_id=project_id,
        ),
        actor_ref="service:issue-433",
    )


async def _canonical_workspace(
    tmp_path: Path,
    *,
    access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE,
    content: bytes = b"canonical workspace bytes",
):
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
    record = await files.create_file(content, context, content_type="text/plain")
    workspaces = LocalWorkspaceProvider(tmp_path / "control-workspaces", files)
    workspace = await workspaces.create_workspace(
        project_id=project_id,
        owner_ref=OwnerRef(type="service", id="issue-433"),
        workspace_type=WorkspaceType.REMOTE,
        context=context,
        access_mode=access_mode,
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
    request = await WorkspaceJobMaterializationResolver(workspaces).resolve(
        WorkerJobRequest(
            execution=_execution_request(project_id),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
        )
    )
    assert request is not None
    return workspaces, files, context, workspace, snapshot, request


def _execution_request(project_id: str):
    from ai_multi_agent_platform.contracts import ExecutionRequest

    task_id = new_id("task")
    return ExecutionRequest(
        run_id=new_id("run"),
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id=f"issue-433:{task_id}",
            project_id=project_id,
        ),
    )


async def _read_file(files: LocalFileProvider, file_id: str, context: DataAccessContext) -> bytes:
    return b"".join([chunk async for chunk in files.stream_file(file_id, context)])


def test_remote_materializer_transfers_chunks_collects_changes_and_cleans_up(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path,
            content=b"a" * 300_000,
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = _RecordingTransport()
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint = WorkerWorkspaceTransportEndpoint(store, transport)
        endpoint_task = asyncio.create_task(endpoint.serve())
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            transport,
            workspaces,
            files,
            lambda _workspace: context,
            chunk_bytes=64 * 1024,
        )
        try:
            receipt = await materializer.materialize(request)
            target = worker_root / workspace.id / snapshot.id / "src" / "input.txt"
            assert target.read_bytes() == b"a" * 300_000
            assert receipt.worker_ref == worker_id
            assert receipt.cache_hit is False
            assert str(tmp_path / "control-workspaces") not in repr(receipt)

            cached = await materializer.materialize(request)
            assert cached.materialization_ref == receipt.materialization_ref
            assert cached.cache_hit is True

            target.write_bytes(b"changed remotely")
            result = await materializer.collect_result(receipt)
            assert len(result.changes) == 1
            change = result.changes[0]
            assert change.kind is WorkspaceChangeKind.MODIFIED
            assert change.file_id is not None
            assert await _read_file(files, change.file_id, context) == b"changed remotely"

            cleanup = await materializer.cleanup(receipt, MaterializationOutcome.SUCCEEDED)
            assert cleanup.succeeded is True
            repeated = await materializer.cleanup(receipt, MaterializationOutcome.SUCCEEDED)
            assert repeated.succeeded is True
            assert not (worker_root / workspace.id / snapshot.id).exists()

            serialized = repr([envelope.to_dict() for _, envelope in transport.published])
            assert str(tmp_path / "control-workspaces") not in serialized
            assert str(worker_root) not in serialized
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_read_only_remote_workspace_detects_worker_side_modification(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path,
            access_mode=WorkspaceAccessMode.READ_ONLY,
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "worker-root"
        transport = InProcessMessageTransport(provider_id="issue-433-read-only")
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
        try:
            receipt = await materializer.materialize(request)
            target = worker_root / workspace.id / snapshot.id / "src" / "input.txt"
            target.chmod(0o600)
            target.write_bytes(b"unauthorized change")
            with pytest.raises(Exception, match="read-only remote Workspace was modified"):
                await materializer.collect_result(receipt)
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_workspace_bound_local_worker_uses_exact_materialized_execution_token(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path
        )
        worker_id = new_id("worker")
        transport = InProcessMessageTransport(provider_id="issue-433-bound-worker")
        store = WorkerWorkspaceMaterializationStore(worker_id, tmp_path / "worker-root")
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
        tokens: list[str] = []

        def lifecycle_factory(token: str) -> FakeLifecycleBackend:
            tokens.append(token)
            return FakeLifecycleBackend()

        worker = WorkspaceBoundLocalWorker(worker_id, store, lifecycle_factory)
        job = WorkerJobRequest(
            execution=_execution_request(workspace.project_id),
            workspace_ref=workspace.id,
            snapshot_ref=snapshot.id,
        )
        try:
            await materializer.materialize(request)
            await worker.dispatch(job)
            assert tokens == [f"{workspace.id}/{snapshot.id}"]
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def _workspace_endpoint_process(host: str, port: int, worker_id: str, root: str) -> None:
    async def serve() -> None:
        transport = TcpMessageTransport(
            host,
            port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id=f"issue-433-process:{worker_id}",
        )
        store = WorkerWorkspaceMaterializationStore(worker_id, root)
        try:
            await WorkerWorkspaceTransportEndpoint(store, transport).serve()
        finally:
            await transport.close(graceful=False)

    asyncio.run(serve())


def test_remote_workspace_materializes_across_independent_worker_process(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path,
            content=b"cross-process-workspace",
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "process-worker-root"
        broker = TcpMessageBroker(authentication_key=TEST_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id="issue-433-process-control",
        )
        process = multiprocessing.get_context("spawn").Process(
            target=_workspace_endpoint_process,
            args=(broker.host, broker.port, worker_id, str(worker_root)),
        )
        process.start()
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            control_transport,
            workspaces,
            files,
            lambda _workspace: context,
            response_timeout_seconds=5.0,
        )
        try:
            receipt = await materializer.materialize(request)
            assert receipt.worker_ref == worker_id
            assert (
                worker_root / workspace.id / snapshot.id / "src" / "input.txt"
            ).read_bytes() == b"cross-process-workspace"
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
