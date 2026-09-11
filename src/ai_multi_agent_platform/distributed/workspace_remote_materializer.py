"""Control-side remote Workspace materialization over the distributed message transport."""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
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

from .registry import RegistryError
from .workspace_transport_codec import (
    _array,
    _decode_cleanup,
    _decode_receipt,
    _encode_receipt,
    _encode_request,
    _ManifestEntry,
    _mapping,
    _required,
    _required_base64,
    _required_integer,
    _required_string,
    _validate_sha256,
)
from .workspace_transport_contract import (
    DEFAULT_WORKSPACE_CHUNK_BYTES,
    WORKSPACE_REPLY_TOPIC_PREFIX,
    WORKSPACE_TRANSPORT_SCHEMA_VERSION,
    worker_workspace_command_topic,
)


class WorkspaceDataContextResolver(Protocol):
    """Resolve one authorized FileProvider context for a canonical Workspace."""

    def __call__(self, workspace: Workspace) -> DataAccessContext: ...


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


__all__ = ["TransportRemoteWorkspaceMaterializer", "WorkspaceDataContextResolver"]
