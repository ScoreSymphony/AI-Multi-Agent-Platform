"""Replaceable notification delivery channels with retry and deduplication semantics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Protocol, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .models import Notification, NotificationPreference, RecipientRef


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
        safe = redact_sensitive(dict(self.metadata))
        if not isinstance(safe, dict):
            raise TypeError("delivery metadata must serialize as a JSON object")
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(cast(dict[str, JsonValue], safe)),
        )


@dataclass(frozen=True, slots=True)
class DeliveryAttempt:
    notification_id: str
    recipient: RecipientRef
    channel: str
    idempotency_key: str
    attempt: int
    status: DeliveryStatus
    attempted_at: datetime
    id: str = field(default_factory=lambda: new_id("notification_delivery"))
    provider_reference: str | None = None
    retry_after_seconds: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "notification_delivery")
        validate_id(self.notification_id, "notification")
        if not self.channel.strip() or not self.idempotency_key.strip():
            raise ValueError("delivery attempt channel/idempotency_key must not be blank")
        if self.attempt < 1:
            raise ValueError("delivery attempt number must be >= 1")
        if self.attempted_at.utcoffset() is None:
            raise ValueError("delivery attempted_at must be timezone-aware")
        if self.provider_reference is not None and not self.provider_reference.strip():
            raise ValueError("delivery provider_reference must not be blank")
        if self.retry_after_seconds is not None and self.retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must not be negative")
        safe = redact_sensitive(dict(self.metadata))
        if not isinstance(safe, dict):
            raise TypeError("delivery attempt metadata must serialize as a JSON object")
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(cast(dict[str, JsonValue], safe)),
        )


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


class DeliveryAttemptRepository(Protocol):
    async def save(self, attempt: DeliveryAttempt) -> DeliveryAttempt: ...

    async def latest(
        self,
        notification_id: str,
        channel: str,
    ) -> DeliveryAttempt | None: ...

    async def list_for_notification(
        self,
        notification_id: str,
    ) -> tuple[DeliveryAttempt, ...]: ...


class InMemoryDeliveryAttemptRepository:
    def __init__(self) -> None:
        self._items: dict[str, DeliveryAttempt] = {}

    async def save(self, attempt: DeliveryAttempt) -> DeliveryAttempt:
        self._items[attempt.id] = attempt
        return attempt

    async def latest(
        self,
        notification_id: str,
        channel: str,
    ) -> DeliveryAttempt | None:
        validate_id(notification_id, "notification")
        candidates = [
            item
            for item in self._items.values()
            if item.notification_id == notification_id and item.channel == channel
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.attempt, item.attempted_at, item.id))

    async def list_for_notification(
        self,
        notification_id: str,
    ) -> tuple[DeliveryAttempt, ...]:
        validate_id(notification_id, "notification")
        items = [
            item for item in self._items.values() if item.notification_id == notification_id
        ]
        items.sort(key=lambda item: (item.channel, item.attempt, item.attempted_at, item.id))
        return tuple(items)


class NotificationDeliveryCoordinator:
    """Route configured channels while preserving stable cross-retry dedupe identity."""

    def __init__(
        self,
        *,
        channels: Mapping[str, NotificationDeliveryChannel] | None = None,
        attempts: DeliveryAttemptRepository | None = None,
    ) -> None:
        self._channels = dict(channels or {})
        self._attempts = attempts or InMemoryDeliveryAttemptRepository()
        for channel_id, channel in self._channels.items():
            if not channel_id.strip() or channel.channel_id != channel_id:
                raise ValueError("delivery channel mapping must match channel.channel_id")

    async def deliver_configured(
        self,
        notification: Notification,
        preference: NotificationPreference,
    ) -> tuple[DeliveryAttempt, ...]:
        attempts: list[DeliveryAttempt] = []
        for channel_id in sorted(preference.external_channels):
            attempts.append(await self.deliver(notification, channel_id=channel_id))
        return tuple(attempts)

    async def deliver(
        self,
        notification: Notification,
        *,
        channel_id: str,
    ) -> DeliveryAttempt:
        if not channel_id.strip():
            raise ValueError("channel_id must not be blank")
        previous = await self._attempts.latest(notification.id, channel_id)
        if previous is not None and previous.status in {
            DeliveryStatus.DELIVERED,
            DeliveryStatus.PERMANENT_FAILURE,
        }:
            return previous

        attempt_number = 1 if previous is None else previous.attempt + 1
        idempotency_key = f"notification:{notification.id}:channel:{channel_id}"
        channel = self._channels.get(channel_id)
        if channel is None:
            return await self._attempts.save(
                DeliveryAttempt(
                    notification_id=notification.id,
                    recipient=notification.recipient,
                    channel=channel_id,
                    idempotency_key=idempotency_key,
                    attempt=attempt_number,
                    status=DeliveryStatus.UNAVAILABLE,
                    attempted_at=datetime.now(UTC),
                    metadata={"reason": "channel_not_configured"},
                )
            )

        try:
            result = await channel.deliver(
                notification,
                recipient=notification.recipient,
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            result = DeliveryResult(
                channel=channel_id,
                status=DeliveryStatus.RETRYABLE_FAILURE,
                metadata={"error_type": type(exc).__name__},
            )
        if result.channel != channel_id:
            result = DeliveryResult(
                channel=channel_id,
                status=DeliveryStatus.PERMANENT_FAILURE,
                metadata={"reason": "channel_identity_mismatch"},
            )
        return await self._attempts.save(
            DeliveryAttempt(
                notification_id=notification.id,
                recipient=notification.recipient,
                channel=channel_id,
                idempotency_key=idempotency_key,
                attempt=attempt_number,
                status=result.status,
                attempted_at=result.attempted_at,
                provider_reference=result.provider_reference,
                retry_after_seconds=result.retry_after_seconds,
                metadata=result.metadata,
            )
        )

    async def list_attempts(self, notification_id: str) -> tuple[DeliveryAttempt, ...]:
        return await self._attempts.list_for_notification(notification_id)


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
