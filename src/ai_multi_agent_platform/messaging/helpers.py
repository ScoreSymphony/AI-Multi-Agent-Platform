from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.domain import Event

from .contracts import MessageTransport
from .models import MessageDelivery, MessageKind, TraceContext, TransportEnvelope


def envelope_for_domain_event(
    event: Event,
    *,
    source_component: str,
    payload_ref: str | None = None,
) -> TransportEnvelope:
    """Create a delivery envelope that references canonical event history.

    The default intentionally carries a reference rather than making transport
    storage a second authoritative event store.
    """

    task_id = event.subject_id if event.subject_type == "task" else None
    run_id = event.subject_id if event.subject_type == "run" else None
    return TransportEnvelope(
        message_type=event.event_type,
        kind=MessageKind.DOMAIN_EVENT,
        payload_schema_version=event.schema_version,
        source_component=source_component,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        project_id=event.project_id,
        task_id=task_id,
        run_id=run_id,
        idempotency_key=event.id,
        trace_context=TraceContext(trace_id=event.trace_id),
        payload_ref=payload_ref or f"canonical-event:{event.id}",
    )


class InMemoryIdempotencyStore:
    """Deterministic process-local coordination for duplicate-safe consumers.

    Only one coroutine may own a key at a time. Concurrent duplicates wait for
    the current owner. If it completes, they observe the completed key; if it
    fails or is cancelled, one waiter may acquire the key and retry processing.
    """

    def __init__(self) -> None:
        self._completed: set[str] = set()
        self._inflight: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> bool:
        """Acquire processing ownership, or return False when already completed."""

        while True:
            async with self._lock:
                if key in self._completed:
                    return False
                wait_for = self._inflight.get(key)
                if wait_for is None:
                    self._inflight[key] = asyncio.Event()
                    return True
            await wait_for.wait()

    async def contains(self, key: str) -> bool:
        async with self._lock:
            return key in self._completed

    async def mark_completed(self, key: str) -> None:
        async with self._lock:
            self._completed.add(key)
            waiters = self._inflight.pop(key, None)
            if waiters is not None:
                waiters.set()

    async def release(self, key: str) -> None:
        """Release an unsuccessful claim so one waiting duplicate can retry."""

        async with self._lock:
            waiters = self._inflight.pop(key, None)
            if waiters is not None:
                waiters.set()


class IdempotentConsumer:
    """Ack duplicates without concurrently repeating an in-process handler.

    This helper does not claim exactly-once side effects. Durable consumers must
    coordinate durable idempotency state with their own side effects when that
    stronger property is required. Process loss can still cause redelivery after
    a side effect because this reference store is intentionally in-memory only.
    """

    def __init__(
        self,
        transport: MessageTransport,
        *,
        store: InMemoryIdempotencyStore | None = None,
    ) -> None:
        self._transport = transport
        self._store = store or InMemoryIdempotencyStore()

    async def handle(
        self,
        delivery: MessageDelivery,
        handler: Callable[[TransportEnvelope], Awaitable[None]],
    ) -> bool:
        key = delivery.envelope.idempotency_key or delivery.envelope.message_id
        if not await self._store.acquire(key):
            await self._transport.ack(delivery)
            return False
        try:
            await handler(delivery.envelope)
        except asyncio.CancelledError:
            await self._store.release(key)
            await self._transport.nack(delivery, retry=True, reason="CancelledError")
            raise
        except Exception as exc:
            await self._store.release(key)
            await self._transport.nack(delivery, retry=True, reason=type(exc).__name__)
            raise
        await self._store.mark_completed(key)
        await self._transport.ack(delivery)
        return True
