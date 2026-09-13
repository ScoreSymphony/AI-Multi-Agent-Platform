from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationPreferenceRepository,
    NotificationCandidate,
    NotificationCategory,
    NotificationQuery,
    NotificationService,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
    SqliteNotificationRepository,
)


def _candidate(user: RecipientRef, task_id: str) -> NotificationCandidate:
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.ERROR,
        title="Task failed",
        summary={"status": "failed"},
        recipient=user,
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:failed",
    )


def test_concurrent_sqlite_aggregation_is_atomic_across_repository_instances(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "notifications.sqlite3"
        user = RecipientRef(RecipientType.USER, new_id("user"))
        candidate = _candidate(user, new_id("task"))
        services = [
            NotificationService(
                repository=SqliteNotificationRepository(database),
                preferences=InMemoryNotificationPreferenceRepository(),
            )
            for _ in range(4)
        ]

        created = await asyncio.gather(
            *(services[index % len(services)].create(candidate) for index in range(12))
        )

        assert all(item is not None for item in created)
        assert len({item.id for item in created if item is not None}) == 1
        items = await services[0].list(NotificationQuery(recipient=user))
        assert len(items) == 1
        assert items[0].occurrence_count == 12

    asyncio.run(scenario())


def test_concurrent_create_once_keeps_single_unincremented_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "notifications.sqlite3"
        user = RecipientRef(RecipientType.USER, new_id("user"))
        candidate = _candidate(user, new_id("task"))
        services = [
            NotificationService(
                repository=SqliteNotificationRepository(database),
                preferences=InMemoryNotificationPreferenceRepository(),
            )
            for _ in range(4)
        ]

        created = await asyncio.gather(
            *(services[index % len(services)].create_once(candidate) for index in range(12))
        )

        assert all(item is not None for item in created)
        assert len({item.id for item in created if item is not None}) == 1
        items = await services[0].list(NotificationQuery(recipient=user))
        assert len(items) == 1
        assert items[0].occurrence_count == 1

    asyncio.run(scenario())
