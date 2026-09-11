"""#35-backed concrete remote Workspace materialization for distributed Workers.

The transport adapter implements the existing #37 ``RemoteWorkspaceMaterializer``
contract. Canonical Workspace, Snapshot and File identity stays on the Control Plane;
Worker-local filesystem paths are deployment details and never cross the wire.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from typing import Protocol, cast
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    ExecutionHandle,
    ExecutionSnapshot,
    LifecycleBackend,
    OperationControl,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, FileRecord
from ai_multi_agent_platform.messaging import (
    MessageKind,
    MessageTransport,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.portability.file_codecs import snapshot_file
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteCleanupAcknowledgement,
    RemoteMaterializationReceipt,
    RemoteMaterializationRequest,
    RemoteMaterializationResult,
    RemoteWorkspaceMaterializer,
    Workspace,
    WorkspaceChange,
    WorkspaceChangeKind,
    WorkspaceProvider,
)

from .models import JobResultStatus, WorkerJobRequest, WorkerJobResult
from .registry import RegistryError
from .worker import LocalWorker, WorkerDispatcher
from .workspace_materialization_store import WorkerWorkspaceMaterializationStore
from .workspace_transport_codec import (
    _ManifestEntry,
    _array,
    _canonical_snapshot_checksum as _canonical_snapshot_checksum,
    _decode_cleanup,
    _decode_receipt,
    _decode_request,
    _encode_cleanup,
    _encode_receipt,
    _encode_request,
    _mapping,
    _required,
    _required_base64,
    _required_integer,
    _required_string,
    _safe_workspace_error,
    _validate_sha256,
)

WORKSPACE_TRANSPORT_SCHEMA_VERSION = "1"
WORKSPACE_COMMAND_TOPIC_PREFIX = "distributed.worker.workspace.commands"
WORKSPACE_REPLY_TOPIC_PREFIX = "distributed.worker.workspace.replies"
DEFAULT_WORKSPACE_CHUNK_BYTES = 128 * 1024


def worker_workspace_command_topic(worker_id: str) -> str:
    if not worker_id.strip():
        raise ValueError("worker_id must not be blank")
    return f"{WORKSPACE_COMMAND_TOPIC_PREFIX}.{worker_id}"


class WorkspaceDataContextResolver(Protocol):
    """Resolve one authorized FileProvider context for a canonical Workspace."""

    def __call__(self, workspace: Workspace) -> DataAccessContext: ...


class WorkspaceLifecycleFactory(Protocol):
    """Build a lifecycle backend bound to one Worker-local execution workspace token."""

    def __call__(self, execution_workspace: str) -> LifecycleBackend: ...


class TransportRemoteWorkspaceMaterializer(RemoteWorkspaceMaterializer):
    """Control-side concrete #37 materializer implemented through #35 transport."""

    def __init__(
        self,
        worker_id: str,
        transport: MessageTransport,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        context_resolver: WorkspaceDataContextResolver,
        *,
        client_id: str = "distributed-workspace-control-plane",
        chunk_bytes: int = DEFAULT_WORKSPACE_CHUNK_BYTES,
        response_timeout_seconds: float = 30.0,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if not client_id.strip():
            raise ValueError("workspace transport client_id must not be blank")
        if chunk_bytes < 1024:
            raise ValueError("workspace transport chunk_bytes must be at least 1024")
        if response_timeout_seconds <= 0:
            raise ValueError("response_timeout_seconds must be greater than zero")
        self.worker_id = worker_id
        self.transport = transport
        self.workspaces = workspaces
        self.files = files
        self.context_resolver = context_resolver
        self.client_id = client_id
        self.chunk_bytes = chunk_bytes
        self.response_timeout_seconds = response_timeout_seconds
        self._results: dict[str, RemoteMaterializationResult] = {}

    async def materialize(
        self,
        request: RemoteMaterializationRequest,
    ) -> RemoteMaterializationReceipt:
        workspace = await self.workspaces.get_workspace(request.workspace_id)
        snapshot = await self.workspaces.get_snapshot(request.snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise RegistryError("remote Workspace snapshot belongs to another Workspace")
        if snapshot.content_checksum != request.expected_checksum:
            raise RegistryError("remote Workspace request checksum differs from canonical snapshot")
        context = self._context(workspace)
        manifest: list[_ManifestEntry] = []
        portable_files: dict[str, bytes] = {}
        for workspace_file in snapshot.files:
            portable = await snapshot_file(self.files, workspace_file.file_id, context)
            if portable.record.sha256 != workspace_file.sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical Workspace File checksum differs from snapshot manifest",
                    details={"file_id": workspace_file.file_id},
                )
            manifest.append(
                _ManifestEntry(
                    relative_path=workspace_file.relative_path,
                    file_id=workspace_file.file_id,
                    sha256=workspace_file.sha256,
                    size_bytes=len(portable.data),
                )
            )
            portable_files[workspace_file.relative_path] = portable.data

        prepared = await self._request(
            request,
            operation="prepare",
            payload={
                "request": _encode_request(request),
                "manifest": [entry.to_json() for entry in manifest],
                "chunk_bytes": self.chunk_bytes,
            },
            idempotency_suffix="prepare",
        )
        receipt_raw = prepared.get("receipt")
        if receipt_raw is not None:
            return _decode_receipt(receipt_raw)
        materialization_ref = _required_string(prepared, "materialization_ref")
        for entry in manifest:
            data = portable_files[entry.relative_path]
            total_chunks = max(1, (len(data) + self.chunk_bytes - 1) // self.chunk_bytes)
            for index in range(total_chunks):
                chunk = data[index * self.chunk_bytes : (index + 1) * self.chunk_bytes]
                await self._request(
                    request,
                    operation="put_chunk",
                    payload={
                        "materialization_ref": materialization_ref,
                        "relative_path": entry.relative_path,
                        "chunk_index": index,
                        "total_chunks": total_chunks,
                        "data_base64": base64.b64encode(chunk).decode("ascii"),
                    },
                    idempotency_suffix=(
                        f"chunk:{entry.file_id}:{index}:{hashlib.sha256(chunk).hexdigest()}"
                    ),
                )
        committed = await self._request(
            request,
            operation="commit",
            payload={"materialization_ref": materialization_ref},
            idempotency_suffix="commit",
        )
        return _decode_receipt(_required(committed, "receipt"))

    async def collect_result(
        self,
        receipt: RemoteMaterializationReceipt,
    ) -> RemoteMaterializationResult:
        existing = self._results.get(receipt.materialization_ref)
        if existing is not None:
            return existing
        workspace = await self.workspaces.get_workspace(receipt.workspace_id)
        context = self._context(workspace)
        manifest_reply = await self._request_for_receipt(
            receipt,
            operation="result_manifest",
            payload={"receipt": _encode_receipt(receipt)},
            idempotency_suffix="result-manifest",
        )
        content_checksum = _required_string(manifest_reply, "content_checksum")
        _validate_sha256(content_checksum)
        changes_raw = _array(_required(manifest_reply, "changes"), "changes")
        changes: list[WorkspaceChange] = []
        for raw in changes_raw:
            change = _mapping(raw, "remote Workspace change")
            relative_path = _required_string(change, "relative_path")
            kind = WorkspaceChangeKind(_required_string(change, "kind"))
            if kind is WorkspaceChangeKind.DELETED:
                changes.append(WorkspaceChange(relative_path=relative_path, kind=kind))
                continue
            sha256 = _required_string(change, "sha256")
            size_bytes = _required_integer(change, "size_bytes", minimum=0)
            data = bytearray()
            total_chunks = max(1, (size_bytes + self.chunk_bytes - 1) // self.chunk_bytes)
            for index in range(total_chunks):
                chunk_reply = await self._request_for_receipt(
                    receipt,
                    operation="result_chunk",
                    payload={
                        "receipt": _encode_receipt(receipt),
                        "relative_path": relative_path,
                        "chunk_index": index,
                        "chunk_bytes": self.chunk_bytes,
                    },
                    idempotency_suffix=f"result-chunk:{relative_path}:{index}",
                )
                data.extend(_required_base64(chunk_reply, "data_base64"))
            raw_data = bytes(data)
            if len(raw_data) != size_bytes or hashlib.sha256(raw_data).hexdigest() != sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "remote Workspace result bytes failed checksum verification",
                )
            file_id = _deterministic_result_file_id(receipt, relative_path, sha256)
            record = await _create_or_reuse_file(
                self.files,
                file_id,
                raw_data,
                context,
                metadata={
                    "workspace_id": receipt.workspace_id,
                    "workspace_snapshot_id": receipt.snapshot_id,
                    "remote_materialization_ref": receipt.materialization_ref,
                    "relative_path": relative_path,
                },
            )
            changes.append(
                WorkspaceChange(
                    relative_path=relative_path,
                    kind=kind,
                    file_id=record.file_id,
                    sha256=record.sha256,
                )
            )
        result = RemoteMaterializationResult(
            workspace_id=receipt.workspace_id,
            snapshot_id=receipt.snapshot_id,
            materialization_ref=receipt.materialization_ref,
            content_checksum=content_checksum,
            changes=tuple(changes),
        )
        self._results[receipt.materialization_ref] = result
        return result

    async def cleanup(
        self,
        receipt: RemoteMaterializationReceipt,
        outcome: MaterializationOutcome,
    ) -> RemoteCleanupAcknowledgement:
        reply = await self._request_for_receipt(
            receipt,
            operation="cleanup",
            payload={
                "receipt": _encode_receipt(receipt),
                "outcome": outcome.value,
            },
            idempotency_suffix=f"cleanup:{outcome.value}",
        )
        return _decode_cleanup(_required(reply, "cleanup"))

    def _context(self, workspace: Workspace) -> DataAccessContext:
        context = self.context_resolver(workspace)
        if context.project_id != workspace.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "remote Workspace data context project does not match Workspace project",
            )
        return context

    async def _request(
        self,
        request: RemoteMaterializationRequest,
        *,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_suffix: str,
    ) -> Mapping[str, object]:
        return await self._send_request(
            correlation_id=f"workspace:{request.workspace_id}:{request.snapshot_id}",
            operation=operation,
            payload=payload,
            idempotency_key=(
                f"workspace:{self.worker_id}:{request.workspace_id}:{request.snapshot_id}:"
                f"{idempotency_suffix}"
            ),
        )

    async def _request_for_receipt(
        self,
        receipt: RemoteMaterializationReceipt,
        *,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_suffix: str,
    ) -> Mapping[str, object]:
        return await self._send_request(
            correlation_id=f"workspace:{receipt.workspace_id}:{receipt.snapshot_id}",
            operation=operation,
            payload=payload,
            idempotency_key=(
                f"workspace:{self.worker_id}:{receipt.materialization_ref}:{idempotency_suffix}"
            ),
        )

    async def _send_request(
        self,
        *,
        correlation_id: str,
        operation: str,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> Mapping[str, object]:
        command_payload: dict[str, JsonValue] = {
            "worker_id": self.worker_id,
            "operation": operation,
            **payload,
        }
        command = TransportEnvelope(
            message_type=f"workspace.{operation}",
            kind=MessageKind.COMMAND,
            payload_schema_version=WORKSPACE_TRANSPORT_SCHEMA_VERSION,
            source_component=self.client_id,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            payload=command_payload,
        )
        reply_topic = f"{WORKSPACE_REPLY_TOPIC_PREFIX}.{command.message_id}"
        command_payload["reply_topic"] = reply_topic
        command = TransportEnvelope(
            message_id=command.message_id,
            message_type=command.message_type,
            kind=command.kind,
            payload_schema_version=command.payload_schema_version,
            source_component=command.source_component,
            correlation_id=command.correlation_id,
            idempotency_key=command.idempotency_key,
            payload=command_payload,
        )
        subscription = self.transport.subscribe(
            Subscription(
                topic=reply_topic,
                consumer_id=f"{self.client_id}:{command.message_id}",
                consumer_group=f"workspace-request:{command.message_id}",
            )
        )
        control = OperationControl(
            timeout_seconds=self.response_timeout_seconds,
            idempotency_key=idempotency_key,
            retry_mode=RetryMode.IDEMPOTENT,
        )
        try:
            await self.transport.publish(
                worker_workspace_command_topic(self.worker_id),
                command,
                control=control,
            )
            try:
                async with asyncio.timeout(self.response_timeout_seconds):
                    delivery = await anext(subscription)
            except TimeoutError as exc:
                raise RegistryError("remote Workspace transport response timed out") from exc
            await self.transport.ack(delivery)
            reply = delivery.envelope
            if reply.causation_id != command.message_id:
                raise RegistryError("remote Workspace reply causation mismatch")
            if reply.correlation_id != command.correlation_id:
                raise RegistryError("remote Workspace reply correlation mismatch")
            data = _mapping(reply.payload, "remote Workspace reply")
            if _required_string(data, "worker_id") != self.worker_id:
                raise RegistryError("remote Workspace reply came from another Worker")
            if reply.message_type == "workspace.error":
                raise RegistryError(_required_string(data, "message"))
            return data
        finally:
            await subscription.aclose()


class WorkerWorkspaceTransportEndpoint:
    """Worker-side consumer for concrete remote Workspace transfer operations."""

    def __init__(
        self,
        store: WorkerWorkspaceMaterializationStore,
        transport: MessageTransport,
        *,
        consumer_id: str | None = None,
    ) -> None:
        self.store = store
        self.transport = transport
        self.consumer_id = consumer_id or f"workspace-endpoint:{store.worker_id}"

    async def serve(self) -> None:
        subscription = self.transport.subscribe(
            Subscription(
                topic=worker_workspace_command_topic(self.store.worker_id),
                consumer_id=self.consumer_id,
                consumer_group=f"workspace-worker:{self.store.worker_id}",
            )
        )
        try:
            async for delivery in subscription:
                try:
                    await self._handle(delivery.envelope)
                except Exception:
                    await self.transport.nack(
                        delivery,
                        retry=True,
                        reason="workspace_transport_reply_publish_failed",
                    )
                else:
                    await self.transport.ack(delivery)
        finally:
            await subscription.aclose()

    async def _handle(self, command: TransportEnvelope) -> None:
        data = _mapping(command.payload, "remote Workspace command")
        reply_topic = _required_string(data, "reply_topic")
        if not reply_topic.startswith(f"{WORKSPACE_REPLY_TOPIC_PREFIX}."):
            raise RegistryError("remote Workspace reply topic is outside canonical prefix")
        if _required_string(data, "worker_id") != self.store.worker_id:
            await self._error(command, reply_topic, "Worker Workspace target mismatch")
            return
        operation = _required_string(data, "operation")
        try:
            payload = await self._dispatch(operation, data)
        except Exception as exc:
            await self._error(command, reply_topic, _safe_workspace_error(exc))
            return
        await self._reply(command, reply_topic, f"workspace.{operation}.accepted", payload)

    async def _dispatch(
        self,
        operation: str,
        data: Mapping[str, object],
    ) -> dict[str, JsonValue]:
        if operation == "prepare":
            request = _decode_request(_required(data, "request"))
            manifest = tuple(
                _ManifestEntry.from_json(item)
                for item in _array(_required(data, "manifest"), "manifest")
            )
            prepared = await self.store.prepare(
                request,
                manifest,
                chunk_bytes=_required_integer(data, "chunk_bytes", minimum=1024),
            )
            if isinstance(prepared, RemoteMaterializationReceipt):
                return {
                    "worker_id": self.store.worker_id,
                    "receipt": _encode_receipt(prepared),
                }
            return {
                "worker_id": self.store.worker_id,
                "materialization_ref": prepared,
            }
        if operation == "put_chunk":
            await self.store.put_chunk(
                _required_string(data, "materialization_ref"),
                _required_string(data, "relative_path"),
                chunk_index=_required_integer(data, "chunk_index", minimum=0),
                total_chunks=_required_integer(data, "total_chunks", minimum=1),
                data=_required_base64(data, "data_base64"),
            )
            return {"worker_id": self.store.worker_id, "stored": True}
        if operation == "commit":
            receipt = await self.store.commit(_required_string(data, "materialization_ref"))
            return {
                "worker_id": self.store.worker_id,
                "receipt": _encode_receipt(receipt),
            }
        if operation == "result_manifest":
            receipt = _decode_receipt(_required(data, "receipt"))
            content_checksum, changes = await self.store.result_manifest(receipt)
            return {
                "worker_id": self.store.worker_id,
                "content_checksum": content_checksum,
                "changes": [
                    {
                        "relative_path": change.relative_path,
                        "kind": change.kind.value,
                        "sha256": change.sha256,
                        "size_bytes": 0 if change.data is None else len(change.data),
                    }
                    for change in changes
                ],
            }
        if operation == "result_chunk":
            receipt = _decode_receipt(_required(data, "receipt"))
            chunk, total_chunks = await self.store.result_chunk(
                receipt,
                _required_string(data, "relative_path"),
                chunk_index=_required_integer(data, "chunk_index", minimum=0),
                chunk_bytes=_required_integer(data, "chunk_bytes", minimum=1024),
            )
            return {
                "worker_id": self.store.worker_id,
                "data_base64": base64.b64encode(chunk).decode("ascii"),
                "total_chunks": total_chunks,
            }
        if operation == "cleanup":
            receipt = _decode_receipt(_required(data, "receipt"))
            cleanup = await self.store.cleanup(
                receipt,
                MaterializationOutcome(_required_string(data, "outcome")),
            )
            return {
                "worker_id": self.store.worker_id,
                "cleanup": _encode_cleanup(cleanup),
            }
        raise RegistryError(f"unsupported remote Workspace operation: {operation}")

    async def _reply(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        message_type: str,
        payload: dict[str, JsonValue],
    ) -> None:
        await self.transport.publish(
            reply_topic,
            TransportEnvelope(
                message_type=message_type,
                kind=MessageKind.SIGNAL,
                payload_schema_version=WORKSPACE_TRANSPORT_SCHEMA_VERSION,
                source_component=f"workspace-worker:{self.store.worker_id}",
                correlation_id=command.correlation_id,
                causation_id=command.message_id,
                payload=payload,
            ),
        )

    async def _error(
        self,
        command: TransportEnvelope,
        reply_topic: str,
        message: str,
    ) -> None:
        await self._reply(
            command,
            reply_topic,
            "workspace.error",
            {
                "worker_id": self.store.worker_id,
                "message": message,
                "retryable": False,
            },
        )


class WorkspaceBoundLocalWorker:
    """Bind canonical Worker jobs to an already-materialized Worker-local Workspace.

    Jobs without Workspace references can delegate to an ordinary fallback dispatcher.
    Workspace jobs receive a lifecycle backend built for the exact local execution token
    resolved from the Worker materialization store.
    """

    def __init__(
        self,
        worker_id: str,
        store: WorkerWorkspaceMaterializationStore,
        lifecycle_factory: WorkspaceLifecycleFactory,
        *,
        fallback: WorkerDispatcher | None = None,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if worker_id != store.worker_id:
            raise ValueError("Workspace store belongs to a different Worker")
        self._worker_id = worker_id
        self._store = store
        self._lifecycle_factory = lifecycle_factory
        self._fallback = fallback
        self._routes: dict[str, WorkerDispatcher] = {}
        self._requests: dict[str, WorkerJobRequest] = {}

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch(self, job: WorkerJobRequest) -> ExecutionHandle:
        existing = self._requests.get(job.worker_job_id)
        if existing is not None:
            if existing != job:
                raise RegistryError("duplicate worker_job_id carries a different Workspace job")
            return await self._routes[job.worker_job_id].dispatch(job)
        if job.workspace_ref is None and job.snapshot_ref is None:
            if self._fallback is None:
                raise RegistryError("Worker job has no materialized Workspace and no fallback")
            route = self._fallback
        elif job.workspace_ref is None or job.snapshot_ref is None:
            raise RegistryError("Workspace Worker jobs require both workspace_ref and snapshot_ref")
        else:
            execution_workspace = self._store.execution_workspace(
                job.workspace_ref,
                job.snapshot_ref,
            )
            route = LocalWorker(
                self.worker_id,
                self._lifecycle_factory(execution_workspace),
            )
        handle = await route.dispatch(job)
        self._routes[job.worker_job_id] = route
        self._requests[job.worker_job_id] = job
        return handle

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).get(worker_job_id)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        return await self._route(worker_job_id).cancel(worker_job_id)

    async def result(self, worker_job_id: str) -> WorkerJobResult | None:
        route = self._route(worker_job_id)
        if isinstance(route, LocalWorker):
            return await route.result(worker_job_id)
        result_method = getattr(route, "result", None)
        if result_method is None:
            snapshot = await route.get(worker_job_id)
            status = _terminal_result_status(snapshot.status.value)
            if status is None:
                return None
            return WorkerJobResult(
                worker_job_id=worker_job_id,
                worker_id=self.worker_id,
                status=status,
                execution=snapshot,
            )
        result = await result_method(worker_job_id)
        return cast(WorkerJobResult | None, result)

    def _route(self, worker_job_id: str) -> WorkerDispatcher:
        try:
            return self._routes[worker_job_id]
        except KeyError as exc:
            raise RegistryError(f"Worker job is unknown locally: {worker_job_id}") from exc


def _terminal_result_status(status: str) -> JobResultStatus | None:
    return {
        "succeeded": JobResultStatus.SUCCEEDED,
        "failed": JobResultStatus.FAILED,
        "cancelled": JobResultStatus.CANCELLED,
        "timed_out": JobResultStatus.TIMED_OUT,
    }.get(status)


async def _create_or_reuse_file(
    files: FileProvider,
    file_id: str,
    data: bytes,
    context: DataAccessContext,
    *,
    metadata: dict[str, JsonValue],
) -> FileRecord:
    try:
        existing = await files.get_file(file_id, context)
    except ContractError as exc:
        if exc.code is not ErrorCode.NOT_FOUND:
            raise
    else:
        if existing.sha256 != hashlib.sha256(data).hexdigest():
            raise ContractError(
                ErrorCode.CONFLICT,
                "deterministic remote Workspace result File ID has different bytes",
            )
        return existing
    return await files.create_file(
        data,
        context,
        file_id=file_id,
        metadata=metadata,
    )


def _deterministic_result_file_id(
    receipt: RemoteMaterializationReceipt,
    relative_path: str,
    sha256: str,
) -> str:
    identity = (
        f"remote-workspace:{receipt.worker_ref}:{receipt.workspace_id}:{receipt.snapshot_id}:"
        f"{receipt.materialization_ref}:{relative_path}:{sha256}"
    )
    return f"file_{uuid5(NAMESPACE_URL, identity)}"
