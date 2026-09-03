from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.accounting import BudgetAction, BudgetThresholdEvent, ThresholdLevel
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import Approval, Event, OwnerRef, new_id
from ai_multi_agent_platform.notifications.integrations import (
    approval_required_candidate,
    budget_threshold_candidate,
)
from ai_multi_agent_platform.notifications.models import (
    NotificationCandidate,
    NotificationCategory,
    NotificationPreference,
    NotificationQuery,
    NotificationSeverity,
    NotificationState,
    RecipientRef,
    RecipientType,
    SourceRef,
)
from ai_multi_agent_platform.notifications.preferences import InMemoryNotificationPreferenceRepository
from ai_multi_agent_platform.notifications.recipients import EventOwnerRecipientResolver
from ai_multi_agent_platform.notifications.repository import InMemoryNotificationRepository
from ai_multi_agent_platform.notifications.rules import TaskTerminalNotificationRule
from ai_multi_agent_platform.notifications.service import NotificationService
from ai_multi_agent_platform.security.approvals import ApprovalRecord
from ai_multi_agent_platform.security.authorization import RiskClassification


USER = RecipientRef(RecipientType.USER, new_id("user"))
OTHER_USER = RecipientRef(RecipientType.USER, new_id("user"))
PROJECT_ID = new_id("project")
TASK_ID = new_id("task")


def _service() -> NotificationService:
    return NotificationService(
        repository=InMemoryNotificationRepository(),
        preferences=InMemoryNotificationPreferenceRepository(),
        rules=(TaskTerminalNotificationRule(EventOwnerRecipientResolver()),),
    )


def _task_event(event_type: str, *, event_id: str | None = None) -> Event:
    return Event(
        id=event_id or new_id("event"),
        event_type=event_type,
        subject_type="task",
        subject_id=TASK_ID,
        owner_ref=OwnerRef(type="user", id=USER.id),
        project_id=PROJECT_ID,
        correlation_id="corr-issue-75",
    )


def test_task_completed_and_failed_events_project_to_canonical_notifications() -> None:
    service = _service()

    completed = asyncio.run(service.project_event(_task_event("task.succeeded")))[0]
    failed = asyncio.run(service.project_event(_task_event("task.failed")))[0]

    assert completed.id.startswith("notification_")
    assert completed.category is NotificationCategory.TASK
    assert completed.severity is NotificationSeverity.INFO
    assert completed.source == SourceRef("task", TASK_ID)
    assert completed.task_id == TASK_ID
    assert failed.severity is NotificationSeverity.ERROR
    assert failed.summary["status"] == "failed"
    assert asyncio.run(service.unread_count(USER)) == 2


def test_duplicate_task_event_aggregates_without_notification_storm() -> None:
    service = _service()
    event = _task_event("task.failed")

    first = asyncio.run(service.project_event(event))[0]
    second = asyncio.run(service.project_event(event))[0]

    assert second.id == first.id
    assert second.occurrence_count == 2
    assert asyncio.run(service.unread_count(USER)) == 1


def test_preference_filters_category_severity_and_project_without_touching_source_state() -> None:
    preferences = InMemoryNotificationPreferenceRepository()
    preferences.save(
        NotificationPreference(
            recipient=USER,
            enabled_categories=frozenset({NotificationCategory.APPROVAL}),
            minimum_severity=NotificationSeverity.WARNING,
            project_ids=frozenset({PROJECT_ID}),
        )
    )
    service = NotificationService(
        repository=InMemoryNotificationRepository(),
        preferences=preferences,
        rules=(TaskTerminalNotificationRule(EventOwnerRecipientResolver()),),
    )

    assert asyncio.run(service.project_event(_task_event("task.succeeded"))) == ()
    assert asyncio.run(service.unread_count(USER)) == 0


def test_read_acknowledge_dismiss_and_mark_all_read_are_deterministic() -> None:
    service = _service()
    first = asyncio.run(service.project_event(_task_event("task.succeeded")))[0]
    second = asyncio.run(service.project_event(_task_event("task.failed")))[0]

    read = asyncio.run(service.mark_read(first.id, recipient=USER))
    reread = asyncio.run(service.mark_read(first.id, recipient=USER))
    assert read.state is NotificationState.READ
    assert reread == read

    acknowledged = asyncio.run(service.acknowledge(first.id, recipient=USER))
    assert acknowledged.state is NotificationState.ACKNOWLEDGED
    assert acknowledged.acknowledged_at is not None

    dismissed = asyncio.run(service.dismiss(first.id, recipient=USER))
    assert dismissed.state is NotificationState.DISMISSED
    assert dismissed.dismissed_at is not None

    updated = asyncio.run(service.mark_all_read(USER))
    assert [item.id for item in updated] == [second.id]
    assert asyncio.run(service.unread_count(USER)) == 0


def test_cross_recipient_notification_access_uses_not_found_semantics() -> None:
    service = _service()
    notification = asyncio.run(service.project_event(_task_event("task.failed")))[0]

    with pytest.raises(ContractError) as caught:
        asyncio.run(service.get(notification.id, recipient=OTHER_USER))

    assert caught.value.code.value == "not_found"


def test_query_filters_unread_category_and_project() -> None:
    service = _service()
    asyncio.run(service.project_event(_task_event("task.succeeded")))
    failed = asyncio.run(service.project_event(_task_event("task.failed")))[0]
    asyncio.run(service.mark_read(failed.id, recipient=USER))

    unread = asyncio.run(
        service.list(
            NotificationQuery(
                recipient=USER,
                category=NotificationCategory.TASK,
                project_id=PROJECT_ID,
                unread_only=True,
            )
        )
    )
    assert len(unread) == 1
    assert unread[0].summary["status"] == "succeeded"


def test_approval_required_projection_uses_exact_approval_reference_without_payload() -> None:
    now = datetime(2026, 9, 3, tzinfo=UTC)
    approval = Approval(
        subject_type="task",
        subject_id=TASK_ID,
        owner_ref=OwnerRef(type="user", id=USER.id),
        reason="sensitive action",
        project_id=PROJECT_ID,
        created_at=now,
        updated_at=now,
    )
    record = ApprovalRecord(
        approval=approval,
        requester_ref=USER.id,
        action="execute",
        resource_type="task",
        resource_id=TASK_ID,
        requested_action_digest="digest-value",
        risk=RiskClassification.ELEVATED,
        policy_id="policy-75",
        expires_at=now + timedelta(minutes=15),
        payload_ref="secret-bearing-payload-ref",
    )

    candidate = approval_required_candidate(record, recipient=USER)

    assert candidate.approval_id == approval.id
    assert candidate.source == SourceRef("approval", approval.id)
    assert candidate.summary["risk"] == "elevated"
    assert "payload_ref" not in candidate.summary
    assert "requested_action_digest" not in candidate.summary


def test_resource_threshold_projection_references_accounting_authority_and_quality() -> None:
    budget_id = f"budget_{new_id('task').removeprefix('task_')}"
    event = BudgetThresholdEvent(
        budget_id=budget_id,
        level=ThresholdLevel.EXCEEDED,
        consumed=120.0,
        limit=100.0,
        metric_type="storage.bytes",
        unit="bytes",
        scope_type="project",
        scope_id=PROJECT_ID,
        action=BudgetAction.NOTIFY,
        budget_version=3,
    )

    candidate = budget_threshold_candidate(
        event,
        recipient=USER,
        measurement_quality="measured",
    )

    assert candidate.category is NotificationCategory.RESOURCE
    assert candidate.severity is NotificationSeverity.ERROR
    assert candidate.source == SourceRef("budget", budget_id)
    assert candidate.summary["measurement_quality"] == "measured"
    assert candidate.summary["budget_version"] == 3


def test_direct_candidate_can_be_created_without_external_delivery_provider() -> None:
    service = _service()
    candidate = NotificationCandidate(
        category=NotificationCategory.GENERAL,
        severity=NotificationSeverity.INFO,
        title="Local notification",
        summary={"message": "in-app baseline"},
        recipient=USER,
        source=SourceRef("task", TASK_ID),
        task_id=TASK_ID,
    )

    created = asyncio.run(service.create(candidate))

    assert created is not None
    assert created.state is NotificationState.UNREAD
