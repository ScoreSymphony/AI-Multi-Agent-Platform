from __future__ import annotations

import asyncio
import multiprocessing
from contextlib import suppress
from pathlib import Path
from typing import Protocol

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionRequest,
    OperationContext,
    OperationControl,
)
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
    MessageDelivery,
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

TEST_TRANSPORT_KEY = "workspace-transport-test-key"
PROCESS_START_TIMEOUT_SECONDS = 15.0


class _ProcessReadySignal(Protocol):
    def set(self) -> None: ...

    def wait(self, timeout: float | None = None) -> bool: ...


class _RecordingTransport(InProcessMessageTransport):
    def __init__(self) -> None:
        super().__init__(provider_id="workspace-recording")
        self.published: list[tuple[str, TransportEnvelope]] = []

    async def _publish_once(
        self,
        topic: str,
        envelope: TransportEnvelope,
    ) -> PublishReceipt:
        self.published.append((topic, envelope))
        return await super()._publish_once(topic, envelope)


class _DropFirstCommitReplyTransport(TcpMessageTransport):
    def __init__(self, host: str, port: int, *, authentication_key: str) -> None:
        super().__init__(
            host,
            port,
            authentication_key=authentication_key,
            provider_id="workspace-drop-commit-reply",
        )
        self.dropped_commit_reply = False

    async def publish(
        self,
        topic: str,
        envelope: TransportEnvelope,
        *,
        control: OperationControl | None = None,
    ) -> PublishReceipt:
        if envelope.message_type == "workspace.commit.accepted" and not self.dropped_commit_reply:
            self.dropped_commit_reply = True
            raise ConnectionError("simulated transient TCP commit reply failure")
        return await super().publish(topic, envelope, control=control)


class _DisconnectAfterFirstChunkAckTransport(TcpMessageTransport):
    def __init__(self, host: str, port: int, *, authentication_key: str) -> None:
        super().__init__(
            host,
            port,
            authentication_key=authentication_key,
            provider_id="workspace-disconnect-after-chunk",
        )
        self.disconnected_after_chunk = False

    async def ack(self, delivery: MessageDelivery) -> None:
        await super().ack(delivery)
        if delivery.envelope.message_type != "workspace.put_chunk" or self.disconnected_after_chunk:
            return
        self.disconnected_after_chunk = True
        for subscription in tuple(self._subscriptions):
            writer = subscription._writer
            if writer is None:
                continue
            writer.close()
            with suppress(ConnectionError, OSError):
                await writer.wait_closed()


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="workspace-transport",
            owner_type="service",
            owner_id="workspace-transport",
            project_id=project_id,
        ),
        actor_ref="service:workspace-transport",
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
        owner_ref=OwnerRef(type="service", id="workspace-transport"),
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


def _execution_request(project_id: str) -> ExecutionRequest:
    task_id = new_id("task")
    return ExecutionRequest(
        run_id=new_id("run"),
        subject_type="task",
        subject_id=task_id,
        context=OperationContext(
            correlation_id=f"workspace-transport:{task_id}",
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
            execution_workspace = store.execution_workspace(workspace.id, snapshot.id)
            target = worker_root / execution_workspace / "src" / "input.txt"
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
            assert not store.has_materialization(workspace.id, snapshot.id)

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
        transport = InProcessMessageTransport(provider_id="workspace-read-only")
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
            execution_workspace = store.execution_workspace(workspace.id, snapshot.id)
            target = worker_root / execution_workspace / "src" / "input.txt"
            target.chmod(0o600)
            target.write_bytes(b"unauthorized change")
            with pytest.raises(
                ContractError,
                match="read-only remote Workspace was modified",
            ) as modified:
                await materializer.collect_result(receipt)
            assert modified.value.code is ErrorCode.FORBIDDEN
            assert modified.value.retryable is False
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
        transport = InProcessMessageTransport(provider_id="workspace-bound-worker")
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
            expected_token = store.execution_workspace(workspace.id, snapshot.id)
            await worker.dispatch(job)
            assert tokens == [expected_token]
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())


def test_tcp_worker_subscription_reconnects_mid_workspace_transfer(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path,
            content=b"r" * 300_000,
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "tcp-reconnect-worker-root"
        broker = TcpMessageBroker(authentication_key=TEST_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id="workspace-reconnect-control",
        )
        worker_transport = _DisconnectAfterFirstChunkAckTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
        )
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, worker_transport).serve()
        )
        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            control_transport,
            workspaces,
            files,
            lambda _workspace: context,
            chunk_bytes=64 * 1024,
            response_timeout_seconds=5.0,
        )
        try:
            receipt = await materializer.materialize(request)
            assert worker_transport.disconnected_after_chunk is True
            assert receipt.worker_ref == worker_id
            execution_workspace = store.execution_workspace(workspace.id, snapshot.id)
            assert (worker_root / execution_workspace / "src" / "input.txt").read_bytes() == (
                b"r" * 300_000
            )
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await worker_transport.close(graceful=False)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())


def test_tcp_commit_reply_failure_redelivers_without_duplicate_materialization(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        workspaces, files, context, workspace, snapshot, request = await _canonical_workspace(
            tmp_path,
            content=b"tcp-redelivery-workspace",
        )
        worker_id = new_id("worker")
        worker_root = tmp_path / "tcp-redelivery-worker-root"
        broker = TcpMessageBroker(authentication_key=TEST_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id="workspace-redelivery-control",
        )
        worker_transport = _DropFirstCommitReplyTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
        )
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, worker_transport).serve()
        )
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
            assert worker_transport.dropped_commit_reply is True
            assert receipt.worker_ref == worker_id
            assert receipt.cache_hit is True
            execution_workspace = store.execution_workspace(workspace.id, snapshot.id)
            assert (worker_root / execution_workspace / "src" / "input.txt").read_bytes() == (
                b"tcp-redelivery-workspace"
            )
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await worker_transport.close(graceful=False)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())


def _workspace_endpoint_process(
    host: str,
    port: int,
    worker_id: str,
    root: str,
    ready: _ProcessReadySignal,
) -> None:
    async def serve() -> None:
        transport = TcpMessageTransport(
            host,
            port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id=f"workspace-process:{worker_id}",
        )
        store = WorkerWorkspaceMaterializationStore(worker_id, root)
        try:
            if not await transport.check_ready():
                raise RuntimeError("spawned Workspace Worker could not reach message broker")
            ready.set()
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
        inspection_store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        broker = TcpMessageBroker(authentication_key=TEST_TRANSPORT_KEY)
        await broker.start()
        control_transport = TcpMessageTransport(
            broker.host,
            broker.port,
            authentication_key=TEST_TRANSPORT_KEY,
            provider_id="workspace-process-control",
        )
        process_context = multiprocessing.get_context("spawn")
        ready = process_context.Event()
        process = process_context.Process(
            target=_workspace_endpoint_process,
            args=(broker.host, broker.port, worker_id, str(worker_root), ready),
        )
        process.start()
        try:
            reached_readiness = await asyncio.to_thread(
                ready.wait,
                PROCESS_START_TIMEOUT_SECONDS,
            )
            if not reached_readiness:
                process.join(timeout=0)
                pytest.fail(
                    "spawned Workspace Worker did not reach transport readiness "
                    f"(exitcode={process.exitcode})"
                )
            assert process.is_alive(), (
                f"spawned Workspace Worker exited after readiness (exitcode={process.exitcode})"
            )
            materializer = TransportRemoteWorkspaceMaterializer(
                worker_id,
                control_transport,
                workspaces,
                files,
                lambda _workspace: context,
                response_timeout_seconds=5.0,
            )
            receipt = await materializer.materialize(request)
            assert receipt.worker_ref == worker_id
            execution_workspace = inspection_store.execution_workspace(workspace.id, snapshot.id)
            assert (worker_root / execution_workspace / "src" / "input.txt").read_bytes() == (
                b"cross-process-workspace"
            )
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            await control_transport.close(graceful=False)
            await broker.close(graceful=False)

    asyncio.run(scenario())
