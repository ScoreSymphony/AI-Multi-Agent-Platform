from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
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
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
    SqliteDeliveryAttemptRepository,
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


def _candidate(recipient: RecipientRef) -> NotificationCandidate:
    task_id = new_id("task")
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.WARNING,
        title="Runtime hardening fixture",
        summary={"fixture": True},
        recipient=recipient,
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:fixture",
    )


def test_single_node_runtime_projects_terminal_task_and_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "single-node")
        first = build_single_node_deployment(config)
        account = first.bootstrap_admin("notification-admin", "correct horse battery staple")
        recipient = RecipientRef(RecipientType.USER, account.user_id)

        smoke = await first.run_reference_smoke()
        tick = await first.control_plane.run_notification_runtime_once()
        projected = await first.control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        terminal = [
            item
            for item in projected
            if item.category is NotificationCategory.TASK
            and item.source.resource_id == smoke.task_id
        ]
        assert tick.examined_events > 0
        assert tick.projected_notifications == 1
        assert len(terminal) == 1
        assert terminal[0].summary["status"] == "succeeded"
        assert terminal[0].occurrence_count == 1

        first.control_plane.notification_service.set_preference(
            NotificationPreference(
                recipient=recipient,
                deadline_reminders_enabled=True,
                deadline_reminder_lead_seconds=3 * 60 * 60,
                overdue_reminders_enabled=False,
                quiet_hours_start="22:00",
                quiet_hours_end="07:00",
                quiet_hours_timezone="Europe/Berlin",
            )
        )
        acknowledged = await first.control_plane.notification_service.acknowledge(
            terminal[0].id,
            recipient=recipient,
        )
        assert acknowledged.state is NotificationState.ACKNOWLEDGED
        assert (config.database_dir / "notifications.sqlite3").is_file()

        restarted = build_single_node_deployment(config)
        second_tick = await restarted.control_plane.run_notification_runtime_once()
        persisted = await restarted.control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        persisted_terminal = [item for item in persisted if item.id == terminal[0].id]
        preference = restarted.control_plane.notification_service.get_preference(recipient)

        assert second_tick.projected_notifications == 0
        assert len(persisted_terminal) == 1
        assert persisted_terminal[0].state is NotificationState.ACKNOWLEDGED
        assert persisted_terminal[0].occurrence_count == 1
        assert preference.deadline_reminder_lead_seconds == 3 * 60 * 60
        assert preference.overdue_reminders_enabled is False
        assert preference.quiet_hours_timezone == "Europe/Berlin"

    asyncio.run(scenario())


def test_mark_read_does_not_downgrade_acknowledged_attention() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
        )
        created = await service.create(_candidate(recipient))
        assert created is not None
        acknowledged = await service.acknowledge(created.id, recipient=recipient)
        reread = await service.mark_read(created.id, recipient=recipient)

        assert acknowledged.state is NotificationState.ACKNOWLEDGED
        assert reread.state is NotificationState.ACKNOWLEDGED
        assert reread.acknowledged_at == acknowledged.acknowledged_at

    asyncio.run(scenario())


def test_quiet_hours_suppress_external_delivery_but_keep_in_app_attention() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                external_channels=frozenset({"fixture"}),
                quiet_hours_start="12:00",
                quiet_hours_end="13:00",
                quiet_hours_timezone="UTC",
            )
        )
        channel = _DeliveredChannel()
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
            delivery=NotificationDeliveryCoordinator(channels={"fixture": channel}),
        )

        created = await service.create(
            _candidate(recipient),
            now=datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        )
        assert created is not None
        assert channel.calls == 0
        assert await service.unread_count(recipient) == 1
        assert await service.delivery_attempts(created.id, recipient=recipient) == ()

    asyncio.run(scenario())


def test_delivery_attempt_dedupe_survives_repository_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
        )
        notification = await service.create(_candidate(recipient))
        assert notification is not None
        db = tmp_path / "notifications.sqlite3"

        first_channel = _DeliveredChannel()
        first = NotificationDeliveryCoordinator(
            channels={"fixture": first_channel},
            attempts=SqliteDeliveryAttemptRepository(db),
        )
        delivered = await first.deliver(notification, channel_id="fixture")
        assert delivered.status is DeliveryStatus.DELIVERED
        assert first_channel.calls == 1

        restarted_channel = _DeliveredChannel()
        restarted = NotificationDeliveryCoordinator(
            channels={"fixture": restarted_channel},
            attempts=SqliteDeliveryAttemptRepository(db),
        )
        duplicate = await restarted.deliver(notification, channel_id="fixture")

        assert duplicate.id == delivered.id
        assert duplicate.attempt == 1
        assert restarted_channel.calls == 0

    asyncio.run(scenario())
