from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    DeliveryResult,
    DeliveryStatus,
    InMemoryDeliveryAttemptRepository,
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationCandidate,
    NotificationCategory,
    NotificationDeliveryCoordinator,
    NotificationPreference,
    NotificationQuery,
    NotificationService,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
)


class _FlakyChannel:
    channel_id = "fixture"

    def __init__(self) -> None:
        self.calls = 0
        self.idempotency_keys: list[str] = []

    async def deliver(
        self,
        notification: object,
        *,
        recipient: RecipientRef,
        idempotency_key: str,
    ) -> DeliveryResult:
        del notification, recipient
        self.calls += 1
        self.idempotency_keys.append(idempotency_key)
        if self.calls == 1:
            return DeliveryResult(
                channel=self.channel_id,
                status=DeliveryStatus.RETRYABLE_FAILURE,
                retry_after_seconds=1,
                metadata={"token": "must-not-leak"},
            )
        return DeliveryResult(
            channel=self.channel_id,
            status=DeliveryStatus.DELIVERED,
            provider_reference="provider-message-1",
        )


def _recipient() -> RecipientRef:
    return RecipientRef(RecipientType.USER, new_id("user"))


def _candidate(recipient: RecipientRef) -> NotificationCandidate:
    task_id = new_id("task")
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.ERROR,
        title="Task failed",
        summary={"status": "failed"},
        recipient=recipient,
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:failed",
    )


def test_in_app_can_be_disabled_without_disabling_configured_external_delivery() -> None:
    async def scenario() -> None:
        recipient = _recipient()
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                in_app_enabled=False,
                external_channels=frozenset({"fixture"}),
            )
        )
        channel = _FlakyChannel()
        delivery = NotificationDeliveryCoordinator(
            channels={"fixture": channel},
            attempts=InMemoryDeliveryAttemptRepository(),
        )
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
            delivery=delivery,
        )

        created = await service.create(_candidate(recipient))
        assert created is not None
        assert channel.calls == 1
        assert await service.list(NotificationQuery(recipient=recipient)) == ()
        assert await service.unread_count(recipient) == 0

        attempts = await service.delivery_attempts(created.id, recipient=recipient)
        assert len(attempts) == 1
        assert attempts[0].status is DeliveryStatus.RETRYABLE_FAILURE
        assert attempts[0].metadata["token"] == "[REDACTED]"

    asyncio.run(scenario())


def test_external_delivery_retry_reuses_stable_idempotency_key_and_dedupes_success() -> None:
    async def scenario() -> None:
        recipient = _recipient()
        channel = _FlakyChannel()
        delivery = NotificationDeliveryCoordinator(channels={"fixture": channel})
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                external_channels=frozenset({"fixture"}),
            )
        )
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
            delivery=delivery,
        )
        created = await service.create(_candidate(recipient))
        assert created is not None

        retry = await service.retry_delivery(
            created.id,
            recipient=recipient,
            channel_id="fixture",
        )
        assert retry.status is DeliveryStatus.DELIVERED
        assert retry.attempt == 2
        assert channel.calls == 2
        assert channel.idempotency_keys[0] == channel.idempotency_keys[1]
        assert channel.idempotency_keys[0] == (
            f"notification:{created.id}:channel:fixture"
        )

        duplicate = await service.retry_delivery(
            created.id,
            recipient=recipient,
            channel_id="fixture",
        )
        assert duplicate.id == retry.id
        assert duplicate.attempt == 2
        assert channel.calls == 2

    asyncio.run(scenario())


def test_unconfigured_external_channel_is_recorded_without_breaking_in_app_notification() -> None:
    async def scenario() -> None:
        recipient = _recipient()
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                external_channels=frozenset({"missing"}),
            )
        )
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
            delivery=NotificationDeliveryCoordinator(),
        )

        created = await service.create(_candidate(recipient))
        assert created is not None
        assert await service.unread_count(recipient) == 1
        attempts = await service.delivery_attempts(created.id, recipient=recipient)
        assert len(attempts) == 1
        assert attempts[0].channel == "missing"
        assert attempts[0].status is DeliveryStatus.UNAVAILABLE
        assert attempts[0].metadata["reason"] == "channel_not_configured"

    asyncio.run(scenario())


def test_delivery_retry_cannot_disclose_or_retry_another_recipients_notification() -> None:
    async def scenario() -> None:
        recipient = _recipient()
        other = _recipient()
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
            delivery=NotificationDeliveryCoordinator(),
        )
        created = await service.create(_candidate(recipient))
        assert created is not None

        with pytest.raises(ContractError) as caught:
            await service.retry_delivery(
                created.id,
                recipient=other,
                channel_id="missing",
            )
        assert caught.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
