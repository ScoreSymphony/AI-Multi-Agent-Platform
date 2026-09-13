from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    NotificationCandidate,
    NotificationCategory,
    NotificationPreference,
    NotificationQuery,
    NotificationService,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
    SqliteNotificationPreferenceRepository,
    SqliteNotificationRepository,
)


def test_sqlite_notification_and_preference_state_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "notifications.sqlite3"
        user = RecipientRef(RecipientType.USER, new_id("user"))
        project_id = new_id("project")
        task_id = new_id("task")

        first = NotificationService(
            repository=SqliteNotificationRepository(db),
            preferences=SqliteNotificationPreferenceRepository(db),
        )
        first.set_preference(
            NotificationPreference(
                recipient=user,
                minimum_severity=NotificationSeverity.WARNING,
                project_ids=frozenset({project_id}),
                external_channels=frozenset({"email"}),
            )
        )
        created = await first.create(
            NotificationCandidate(
                category=NotificationCategory.TASK,
                severity=NotificationSeverity.ERROR,
                title="Task failed",
                summary={"status": "failed"},
                recipient=user,
                source=SourceRef("task", task_id),
                project_id=project_id,
                task_id=task_id,
                aggregation_key=f"task:{task_id}:failed",
            )
        )
        assert created is not None
        await first.mark_read(created.id, recipient=user)

        restarted = NotificationService(
            repository=SqliteNotificationRepository(db),
            preferences=SqliteNotificationPreferenceRepository(db),
        )
        restored = await restarted.get(created.id, recipient=user)
        preference = restarted.get_preference(user)

        assert restored.state is NotificationState.READ
        assert restored.read_at is not None
        assert restored.source.resource_id == task_id
        assert preference.minimum_severity is NotificationSeverity.WARNING
        assert preference.project_ids == frozenset({project_id})
        assert preference.external_channels == frozenset({"email"})
        assert await restarted.unread_count(user) == 0

    asyncio.run(scenario())


def test_sqlite_duplicate_aggregation_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        db = tmp_path / "notifications.sqlite3"
        user = RecipientRef(RecipientType.USER, new_id("user"))
        task_id = new_id("task")
        candidate = NotificationCandidate(
            category=NotificationCategory.TASK,
            severity=NotificationSeverity.ERROR,
            title="Task failed",
            summary={"status": "failed"},
            recipient=user,
            source=SourceRef("task", task_id),
            task_id=task_id,
            aggregation_key=f"task:{task_id}:failed",
        )

        first = NotificationService(
            repository=SqliteNotificationRepository(db),
            preferences=SqliteNotificationPreferenceRepository(db),
        )
        created = await first.create(candidate)
        assert created is not None

        restarted = NotificationService(
            repository=SqliteNotificationRepository(db),
            preferences=SqliteNotificationPreferenceRepository(db),
        )
        aggregated = await restarted.create(candidate)
        assert aggregated is not None
        assert aggregated.id == created.id
        assert aggregated.occurrence_count == 2

        items = await restarted.list(NotificationQuery(recipient=user))
        assert len(items) == 1
        assert items[0].occurrence_count == 2

    asyncio.run(scenario())
