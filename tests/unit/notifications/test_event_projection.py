from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import Event, OwnerRef, new_id
from ai_multi_agent_platform.notifications import (
    EventOwnerRecipientResolver,
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationProjectingEventProvider,
    NotificationQuery,
    NotificationService,
    RecipientRef,
    RecipientType,
    TaskTerminalNotificationRule,
)
from ai_multi_agent_platform.testing import FakeEventProvider


def test_event_provider_projects_task_event_and_replay_aggregates_safely() -> None:
    async def scenario() -> None:
        repository = InMemoryNotificationRepository()
        preferences = InMemoryNotificationPreferenceRepository()
        service = NotificationService(repository=repository, preferences=preferences, rules=(TaskTerminalNotificationRule(EventOwnerRecipientResolver()),))
        inner = FakeEventProvider()
        provider = NotificationProjectingEventProvider(inner, service)
        user_id = new_id("user")
        task_id = new_id("task")
        event = Event(event_type="task.failed", subject_type="task", subject_id=task_id, correlation_id=task_id, owner_ref=OwnerRef(type="user", id=user_id))
        await provider.publish(event)
        recipient = RecipientRef(RecipientType.USER, user_id)
        inbox = await service.list(NotificationQuery(recipient=recipient))
        assert len(inbox) == 1
        assert inbox[0].occurrence_count == 1
        projected = await provider.replay((event,))
        assert projected == 1
        replayed = await service.list(NotificationQuery(recipient=recipient))
        assert len(replayed) == 1
        assert replayed[0].occurrence_count == 2
    asyncio.run(scenario())
