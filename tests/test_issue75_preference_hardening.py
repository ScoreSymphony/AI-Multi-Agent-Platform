from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    DeliveryResult,
    DeliveryStatus,
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


class _DeliveredChannel:
    channel_id = "fixture"

    def __init__(self) -> None:
        self.calls = 0

    async def deliver(
        self,
        notification: object,
        *,
        recipient: RecipientRef,
        idempotency_key: str,
    ) -> DeliveryResult:
        del notification, recipient, idempotency_key
        self.calls += 1
        return DeliveryResult(
            channel=self.channel_id,
            status=DeliveryStatus.DELIVERED,
            provider_reference=f"message-{self.calls}",
        )


def _deadline_candidate(recipient: RecipientRef, phase: str) -> NotificationCandidate:
    task_id = new_id("task")
    return NotificationCandidate(
        category=NotificationCategory.DEADLINE,
        severity=NotificationSeverity.WARNING,
        title="Task deadline",
        summary={"phase": phase},
        recipient=recipient,
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:deadline:{phase}",
    )


def test_deadline_preferences_filter_approaching_and_overdue_independently() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                deadline_reminders_enabled=False,
                overdue_reminders_enabled=True,
            )
        )
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
        )

        approaching = await service.create(_deadline_candidate(recipient, "approaching"))
        overdue = await service.create(_deadline_candidate(recipient, "overdue"))

        assert approaching is None
        assert overdue is not None
        assert await service.unread_count(recipient) == 1

    asyncio.run(scenario())


def test_quiet_hours_suppress_external_delivery_but_keep_in_app_attention() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                external_channels=frozenset({"fixture"}),
                quiet_hours_start="22:00",
                quiet_hours_end="07:00",
                quiet_hours_timezone="Europe/Berlin",
            )
        )
        channel = _DeliveredChannel()
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
            delivery=NotificationDeliveryCoordinator(channels={"fixture": channel}),
        )

        created = await service.create(
            _deadline_candidate(recipient, "overdue"),
            now=datetime(2026, 9, 4, 21, 30, tzinfo=UTC),
        )

        assert created is not None
        assert channel.calls == 0
        assert await service.unread_count(recipient) == 1
        assert len(await service.list(NotificationQuery(recipient=recipient))) == 1

    asyncio.run(scenario())


def test_quiet_hours_require_complete_valid_configuration() -> None:
    recipient = RecipientRef(RecipientType.USER, new_id("user"))

    with pytest.raises(ValueError, match="must be set together"):
        NotificationPreference(recipient=recipient, quiet_hours_start="22:00")

    with pytest.raises(ValueError, match="valid IANA timezone"):
        NotificationPreference(
            recipient=recipient,
            quiet_hours_start="22:00",
            quiet_hours_end="07:00",
            quiet_hours_timezone="Not/AZone",
        )
