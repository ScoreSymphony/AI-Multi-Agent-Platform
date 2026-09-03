"""Narrow adapters from completed canonical domains into notification candidates."""

from __future__ import annotations

from ai_multi_agent_platform.accounting.models import BudgetThresholdEvent, ThresholdLevel
from ai_multi_agent_platform.security.approvals import ApprovalRecord

from .models import (
    NotificationAction,
    NotificationCandidate,
    NotificationCategory,
    NotificationSeverity,
    RecipientRef,
    SourceRef,
)


def approval_required_candidate(
    approval: ApprovalRecord,
    *,
    recipient: RecipientRef,
) -> NotificationCandidate:
    """Project an already-canonical pending approval into user attention.

    The caller resolves the approver recipient according to #15/#87 policy. The notification
    does not infer an approver from the requester and does not copy proposed payload data.
    """

    return NotificationCandidate(
        category=NotificationCategory.APPROVAL,
        severity=NotificationSeverity.WARNING,
        title="Approval required",
        summary={
            "approval_id": approval.approval_id,
            "action": approval.action,
            "resource_type": approval.resource_type,
            "resource_id": approval.resource_id,
            "risk": approval.risk.value,
            "policy_id": approval.policy_id,
            "expires_at": approval.expires_at.isoformat(),
        },
        recipient=recipient,
        source=SourceRef(resource_type="approval", resource_id=approval.approval_id),
        project_id=approval.project_id,
        task_id=approval.task_id,
        run_id=approval.run_id,
        approval_id=approval.approval_id,
        actions=(
            NotificationAction(
                action_id="review",
                label="Review approval",
                resource_type="approval",
                resource_id=approval.approval_id,
                href=f"/approvals/{approval.approval_id}",
            ),
        ),
        aggregation_key=f"approval:{approval.approval_id}:pending",
    )


def budget_threshold_candidate(
    event: BudgetThresholdEvent,
    *,
    recipient: RecipientRef,
    measurement_quality: str | None = None,
) -> NotificationCandidate:
    """Project #76 threshold state without becoming the budget/accounting authority."""

    severity = (
        NotificationSeverity.ERROR
        if event.level is ThresholdLevel.EXCEEDED
        else NotificationSeverity.WARNING
    )
    summary = {
        "budget_id": event.budget_id,
        "level": event.level.value,
        "consumed": event.consumed,
        "limit": event.limit,
        "metric_type": event.metric_type,
        "unit": event.unit,
        "scope_type": event.scope_type,
        "scope_id": event.scope_id,
        "budget_version": event.budget_version,
        "measurement_quality": measurement_quality,
    }
    return NotificationCandidate(
        category=NotificationCategory.RESOURCE,
        severity=severity,
        title=(
            "Resource budget exceeded"
            if event.level is ThresholdLevel.EXCEEDED
            else "Resource budget warning"
        ),
        summary=summary,
        recipient=recipient,
        source=SourceRef(resource_type="budget", resource_id=event.budget_id),
        aggregation_key=f"budget:{event.budget_id}:{event.budget_version}:{event.level.value}",
        causation_id=event.id,
    )
