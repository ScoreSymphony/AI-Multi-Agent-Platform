"""Worker-side command endpoint for distributed remote Workspace transfer operations."""

from __future__ import annotations

import base64
from collections.abc import Mapping

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.messaging import (
    MessageKind,
    MessageTransport,
    Subscription,
    TransportEnvelope,
)
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RemoteMaterializationReceipt,
)

from .registry import RegistryError
from .workspace_materialization_store import WorkerWorkspaceMaterializationStore
from .workspace_transport_codec import (
    _ManifestEntry,
    _array,
    _decode_receipt,
    _decode_request,
    _encode_cleanup,
    _encode_receipt,
    _mapping,
    _required,
    _required_base64,
    _required_integer,
    _required_string,
    _safe_workspace_error,
)
from .workspace_transport_contract import (
    WORKSPACE_REPLY_TOPIC_PREFIX,
    WORKSPACE_TRANSPORT_SCHEMA_VERSION,
    worker_workspace_command_topic,
)


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


__all__ = ["WorkerWorkspaceTransportEndpoint"]
