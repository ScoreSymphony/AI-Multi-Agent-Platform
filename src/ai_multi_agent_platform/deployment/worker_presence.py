"""Deployment-only Worker reachability probes over the existing #35 transport.

A Node reporter remains the sole owner of canonical Node heartbeat state. These probes provide
transport reachability evidence for independently running sibling Worker processes so a live
reporter cannot accidentally keep a dead sibling schedulable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.messaging import (
    MessageKind,
    MessageTransport,
    Subscription,
    TransportEnvelope,
)

_WORKER_PRESENCE_SCHEMA_VERSION = "1"
_WORKER_PRESENCE_TOPIC_PREFIX = "distributed.worker.presence"
_WORKER_PRESENCE_REPLY_PREFIX = "distributed.worker.presence.replies"


def worker_presence_topic(worker_id: str) -> str:
    if not worker_id.strip():
        raise ValueError("worker_id must not be blank")
    return f"{_WORKER_PRESENCE_TOPIC_PREFIX}.{worker_id}"


class TransportWorkerPresenceProbe:
    """Control-side bounded probe for one Worker endpoint on the configured #35 transport."""

    def __init__(
        self,
        transport: MessageTransport,
        *,
        timeout_seconds: float = 1.0,
        client_id: str = "distributed-control-plane",
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Worker presence timeout_seconds must be greater than zero")
        if not client_id.strip():
            raise ValueError("Worker presence client_id must not be blank")
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._client_id = client_id

    async def reachable(self, worker_id: str) -> bool:
        message_id = f"message_{uuid4()}"
        correlation_id = f"worker-presence:{worker_id}:{uuid4().hex}"
        reply_topic = f"{_WORKER_PRESENCE_REPLY_PREFIX}.{message_id}"
        envelope = TransportEnvelope(
            message_id=message_id,
            message_type="worker.presence.probe",
            kind=MessageKind.SIGNAL,
            payload_schema_version=_WORKER_PRESENCE_SCHEMA_VERSION,
            source_component=self._client_id,
            correlation_id=correlation_id,
            payload={
                "worker_id": worker_id,
                "reply_topic": reply_topic,
            },
        )
        subscription = self._transport.subscribe(
            Subscription(
                topic=reply_topic,
                consumer_id=f"presence:{self._client_id}:{message_id}",
                consumer_group=f"presence-request:{message_id}",
            )
        )
        try:
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    await self._transport.publish(worker_presence_topic(worker_id), envelope)
                    delivery = await subscription.__anext__()
            except (ContractError, TimeoutError, OSError):
                return False
            reply = delivery.envelope
            try:
                data = _mapping(reply.payload, "Worker presence reply")
                valid = (
                    reply.message_type == "worker.presence.ready"
                    and reply.causation_id == message_id
                    and reply.correlation_id == correlation_id
                    and _required_string(data, "worker_id") == worker_id
                )
            except (TypeError, ValueError):
                valid = False
            await self._transport.ack(delivery)
            return valid
        finally:
            await subscription.aclose()


class WorkerPresenceEndpoint:
    """Worker-side responder proving that this exact Worker process is transport-reachable."""

    def __init__(self, worker_id: str, transport: MessageTransport) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        self.worker_id = worker_id
        self._transport = transport

    async def serve(self) -> None:
        subscription = self._transport.subscribe(
            Subscription(
                topic=worker_presence_topic(self.worker_id),
                consumer_id=f"presence-endpoint:{self.worker_id}",
                consumer_group=f"presence-worker:{self.worker_id}",
            )
        )
        try:
            async for delivery in subscription:
                try:
                    await self._reply(delivery.envelope)
                except (ContractError, TypeError, ValueError):
                    await self._transport.nack(
                        delivery,
                        retry=False,
                        reason="invalid_worker_presence_probe",
                    )
                else:
                    await self._transport.ack(delivery)
        finally:
            await subscription.aclose()

    async def _reply(self, probe: TransportEnvelope) -> None:
        if probe.message_type != "worker.presence.probe":
            raise ValueError("unexpected Worker presence message type")
        data = _mapping(probe.payload, "Worker presence probe")
        if _required_string(data, "worker_id") != self.worker_id:
            raise ValueError("Worker presence probe targets a different Worker")
        reply_topic = _required_string(data, "reply_topic")
        if not reply_topic.startswith(f"{_WORKER_PRESENCE_REPLY_PREFIX}."):
            raise ValueError("Worker presence reply topic is outside the deployment prefix")
        reply = TransportEnvelope(
            message_type="worker.presence.ready",
            kind=MessageKind.SIGNAL,
            payload_schema_version=_WORKER_PRESENCE_SCHEMA_VERSION,
            source_component=f"worker:{self.worker_id}",
            correlation_id=probe.correlation_id,
            causation_id=probe.message_id,
            payload={"worker_id": self.worker_id},
        )
        await self._transport.publish(reply_topic, reply)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    return value


def _required_string(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-blank string")
    return value
