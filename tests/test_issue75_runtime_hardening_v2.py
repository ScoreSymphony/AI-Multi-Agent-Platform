from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.notifications import (
    DeliveryResult,
    DeliveryStatus,
    NotificationCategory,
    NotificationDeliveryCoordinator,
    NotificationQuery,
    RecipientRef,
    RecipientType,
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
        assert (config.database_dir / "notifications.sqlite3").is_file()

        restarted = build_single_node_deployment(config)
        second_tick = await restarted.control_plane.run_notification_runtime_once()
        persisted = await restarted.control_plane.notification_service.list(
            NotificationQuery(recipient=recipient)
        )
        persisted_terminal = [item for item in persisted if item.id == terminal[0].id]

        assert second_tick.projected_notifications == 0
        assert len(persisted_terminal) == 1
        assert persisted_terminal[0].occurrence_count == 1

    asyncio.run(scenario())


def test_single_node_asgi_lifespan_starts_both_runtime_loops(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "single-node")
        )
        received = iter(
            [
                {"type": "lifespan.startup"},
                {"type": "lifespan.shutdown"},
            ]
        )
        sent: list[dict[str, Any]] = []
        saw_running = False

        async def receive() -> dict[str, Any]:
            nonlocal saw_running
            message = next(received)
            if message["type"] == "lifespan.shutdown":
                saw_running = (
                    deployment.control_plane.automation_runtime.running
                    and deployment.control_plane.notification_runtime.running
                )
            return message

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await deployment.app({"type": "lifespan"}, receive, send)

        assert saw_running is True
        assert deployment.control_plane.automation_runtime.running is False
        assert deployment.control_plane.notification_runtime.running is False
        assert [message["type"] for message in sent] == [
            "lifespan.startup.complete",
            "lifespan.shutdown.complete",
        ]

    asyncio.run(scenario())


def test_delivery_attempt_dedupe_survives_repository_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        from ai_multi_agent_platform.domain import new_id
        from ai_multi_agent_platform.notifications import (
            InMemoryNotificationPreferenceRepository,
            InMemoryNotificationRepository,
            NotificationCandidate,
            NotificationService,
            NotificationSeverity,
            SourceRef,
        )

        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        task_id = new_id("task")
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
        )
        notification = await service.create(
            NotificationCandidate(
                category=NotificationCategory.TASK,
                severity=NotificationSeverity.INFO,
                title="Delivery fixture",
                summary={"fixture": True},
                recipient=recipient,
                source=SourceRef("task", task_id),
                task_id=task_id,
            )
        )
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
