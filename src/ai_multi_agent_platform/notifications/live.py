"""Recipient-scoped live notification event delivery.

The live hub is a transport projection only. Notification state remains authoritative in the
notification repository, so clients can always recover by re-reading the canonical inbox.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .models import RecipientRef, RecipientType


@dataclass(frozen=True, slots=True)
class NotificationLiveEvent:
    event: str
    recipient: RecipientRef
    payload: Mapping[str, JsonValue]
    id: str = field(default_factory=lambda: f"notification_event_{uuid4()}")
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if not self.event.strip():
            raise ValueError("notification live event type must not be blank")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("notification live event timestamp must be timezone-aware")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "event": self.event,
            "recipient": {
                "type": self.recipient.type.value,
                "id": self.recipient.id,
            },
            "occurred_at": self.occurred_at.isoformat(),
            "payload": dict(self.payload),
        }


class NotificationLiveHub:
    """Small in-process SSE fan-out with bounded reconnect replay.

    The hub deliberately does not become durable notification truth. Its bounded history only
    bridges short browser reconnects; a stale/missing cursor is surfaced so the client can
    refresh the canonical inbox from the Control Plane.
    """

    def __init__(self, *, history_limit: int = 256, queue_limit: int = 256) -> None:
        if history_limit < 1 or queue_limit < 1:
            raise ValueError("notification live hub limits must be positive")
        self._history_limit = history_limit
        self._queue_limit = queue_limit
        self._history: dict[RecipientRef, deque[NotificationLiveEvent]] = defaultdict(
            lambda: deque(maxlen=self._history_limit)
        )
        self._subscribers: dict[RecipientRef, set[asyncio.Queue[NotificationLiveEvent]]] = (
            defaultdict(set)
        )

    async def publish(self, payload: dict[str, JsonValue]) -> None:
        recipient = _recipient_from_payload(payload)
        if recipient is None:
            return
        raw_event = payload.get("event")
        if not isinstance(raw_event, str) or not raw_event.strip():
            return
        safe = redact_sensitive(payload)
        if not isinstance(safe, dict):
            return
        event = NotificationLiveEvent(
            event=raw_event,
            recipient=recipient,
            payload={key: value for key, value in safe.items() if key != "event"},
        )
        self._history[recipient].append(event)
        for queue in tuple(self._subscribers.get(recipient, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    def subscribe(
        self,
        recipient: RecipientRef,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[NotificationLiveEvent]:
        replay = self._replay(recipient, after_event_id)
        queue: asyncio.Queue[NotificationLiveEvent] = asyncio.Queue(maxsize=self._queue_limit)
        self._subscribers[recipient].add(queue)

        async def iterator() -> AsyncIterator[NotificationLiveEvent]:
            try:
                for event in replay:
                    yield event
                while True:
                    yield await queue.get()
            finally:
                subscribers = self._subscribers.get(recipient)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(recipient, None)

        return iterator()

    def _replay(
        self,
        recipient: RecipientRef,
        after_event_id: str | None,
    ) -> tuple[NotificationLiveEvent, ...]:
        history = tuple(self._history.get(recipient, ()))
        if after_event_id is None:
            return ()
        for index, event in enumerate(history):
            if event.id == after_event_id:
                return history[index + 1 :]
        raise ContractError(
            ErrorCode.NOT_FOUND,
            "notification live cursor is unavailable; refresh the canonical inbox",
        )


NotificationEventSink = Callable[[dict[str, JsonValue]], Awaitable[None]]


def fanout_notification_event_sinks(
    *sinks: NotificationEventSink | None,
) -> NotificationEventSink:
    active = tuple(sink for sink in sinks if sink is not None)

    async def publish(payload: dict[str, JsonValue]) -> None:
        for sink in active:
            await sink(dict(payload))

    return publish


def _recipient_from_payload(payload: Mapping[str, JsonValue]) -> RecipientRef | None:
    raw_type = payload.get("recipient_type")
    raw_id = payload.get("recipient_id")
    if not isinstance(raw_type, str) or not isinstance(raw_id, str):
        return None
    try:
        return RecipientRef(type=RecipientType(raw_type), id=raw_id)
    except ValueError:
        return None
