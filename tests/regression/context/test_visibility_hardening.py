from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationCandidate,
    NotificationCategory,
    NotificationQuery,
    NotificationService,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from ai_multi_agent_platform.notifications.models import Notification


class _SelectiveVisibility:
    def __init__(self, denied_source_ids: set[str]) -> None:
        self._denied_source_ids = denied_source_ids

    async def allows(self, notification: Notification, *, recipient: RecipientRef) -> bool:
        del recipient
        return notification.source.resource_id not in self._denied_source_ids


def _candidate(recipient: RecipientRef, task_id: str) -> NotificationCandidate:
    return NotificationCandidate(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.INFO,
        title="Task attention",
        summary={"status": "succeeded"},
        recipient=recipient,
        source=SourceRef("task", task_id),
        task_id=task_id,
        aggregation_key=f"task:{task_id}:terminal",
    )


def test_revoked_source_visibility_is_hidden_from_inbox_count_and_actions() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        visible_task_id = new_id("task")
        hidden_task_id = new_id("task")
        repository = InMemoryNotificationRepository()
        service = NotificationService(
            repository=repository,
            preferences=InMemoryNotificationPreferenceRepository(),
            visibility=_SelectiveVisibility({hidden_task_id}),
        )

        visible = await service.create(_candidate(recipient, visible_task_id))
        hidden = await service.create(_candidate(recipient, hidden_task_id))
        assert visible is not None
        assert hidden is not None

        inbox = await service.list(NotificationQuery(recipient=recipient))
        assert [item.id for item in inbox] == [visible.id]
        assert await service.unread_count(recipient) == 1

        with pytest.raises(ContractError) as exc_info:
            await service.get(hidden.id, recipient=recipient)
        assert exc_info.value.code is ErrorCode.NOT_FOUND

        marked = await service.mark_all_read(recipient)
        assert [item.id for item in marked] == [visible.id]
        assert (await repository.get(visible.id)).state is NotificationState.READ
        assert (await repository.get(hidden.id)).state is NotificationState.UNREAD

    asyncio.run(scenario())


def test_mark_read_preserves_acknowledged_state() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=InMemoryNotificationPreferenceRepository(),
        )
        created = await service.create(_candidate(recipient, new_id("task")))
        assert created is not None

        acknowledged = await service.acknowledge(created.id, recipient=recipient)
        reread = await service.mark_read(created.id, recipient=recipient)

        assert acknowledged.state is NotificationState.ACKNOWLEDGED
        assert reread.state is NotificationState.ACKNOWLEDGED
        assert reread.acknowledged_at == acknowledged.acknowledged_at

    asyncio.run(scenario())
