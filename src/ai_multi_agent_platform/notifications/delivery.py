"""Replaceable notification delivery-channel boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Mapping, Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import Notification, RecipientRef


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    channel: str
    status: DeliveryStatus
    attempted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider_reference: str | None = None
    retry_after_seconds: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.channel.strip():
            raise ValueError("delivery channel must not be blank")
        if self.attempted_at.utcoffset() is None:
            raise ValueError("attempted_at must be timezone-aware")
        if self.provider_reference is not None and not self.provider_reference.strip():
            raise ValueError("provider_reference must not be blank")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")


class NotificationDeliveryChannel(Protocol):
    """Provider-neutral bridge used by future Connector-backed external channels."""

    @property
    def channel_id(self) -> str: ...

    async def deliver(
        self,
        notification: Notification,
        *,
        recipient: RecipientRef,
        idempotency_key: str,
    ) -> DeliveryResult: ...


class UnavailableDeliveryChannel:
    """Deterministic fixture proving external delivery is optional."""

    def __init__(self, channel_id: str) -> None:
        if not channel_id.strip():
            raise ValueError("channel_id must not be blank")
        self._channel_id = channel_id

    @property
    def channel_id(self) -> str:
        return self._channel_id

    async def deliver(
        self,
        notification: Notification,
        *,
        recipient: RecipientRef,
        idempotency_key: str,
    ) -> DeliveryResult:
        del notification, recipient, idempotency_key
        return DeliveryResult(channel=self.channel_id, status=DeliveryStatus.UNAVAILABLE)
