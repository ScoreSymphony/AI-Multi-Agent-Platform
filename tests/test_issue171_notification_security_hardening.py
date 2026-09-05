from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.accounting import BudgetAction, BudgetThresholdEvent, ThresholdLevel
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.notifications import (
    NotificationCategory,
    NotificationPreference,
    NotificationQuery,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    budget_threshold_candidate,
)
from ai_multi_agent_platform.notifications.preferences import (
    InMemoryNotificationPreferenceRepository,
)
from ai_multi_agent_platform.notifications.repository import InMemoryNotificationRepository
from ai_multi_agent_platform.notifications.service import NotificationService


def test_budget_notification_honors_preferences_and_recipient_isolation() -> None:
    async def scenario() -> None:
        recipient = RecipientRef(RecipientType.USER, new_id("user"))
        other = RecipientRef(RecipientType.USER, new_id("user"))
        preferences = InMemoryNotificationPreferenceRepository()
        preferences.save(
            NotificationPreference(
                recipient=recipient,
                enabled_categories=frozenset({NotificationCategory.APPROVAL}),
                minimum_severity=NotificationSeverity.INFO,
            )
        )
        service = NotificationService(
            repository=InMemoryNotificationRepository(),
            preferences=preferences,
        )
        event = BudgetThresholdEvent(
            budget_id=f"budget_{new_id('task').removeprefix('task_')}",
            level=ThresholdLevel.EXCEEDED,
            consumed=120.0,
            limit=100.0,
            metric_type="storage.file.bytes.current",
            unit="bytes",
            scope_type="project",
            scope_id=new_id("project"),
            action=BudgetAction.NOTIFY,
            budget_version=1,
        )
        candidate = budget_threshold_candidate(
            event,
            recipient=recipient,
            measurement_quality="reported",
            threshold_generation=1,
        )

        assert await service.create_once(candidate) is None
        assert await service.list(NotificationQuery(recipient=recipient)) == ()

        preferences.save(
            NotificationPreference(
                recipient=recipient,
                enabled_categories=frozenset({NotificationCategory.RESOURCE}),
                minimum_severity=NotificationSeverity.INFO,
            )
        )
        created = await service.create_once(candidate)
        assert created is not None
        assert created.recipient == recipient
        assert created.category is NotificationCategory.RESOURCE
        assert created.summary["measurement_quality"] == "reported"
        assert created.summary["threshold_generation"] == 1
        assert set(created.summary) == {
            "budget_id",
            "level",
            "consumed",
            "limit",
            "metric_type",
            "unit",
            "scope_type",
            "scope_id",
            "budget_version",
            "measurement_quality",
            "threshold_generation",
        }

        with pytest.raises(ContractError) as caught:
            await service.get(created.id, recipient=other)
        assert caught.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
