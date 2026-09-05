"""Narrow adapters from canonical domains into notification candidates.

Completed domains may be referenced directly. Domain integrations preserve canonical source
identity and attention semantics without making Notifications their lifecycle authority.
"""

from __future__ import annotations

from ai_multi_agent_platform.accounting.models import BudgetThresholdEvent, ThresholdLevel
from ai_multi_agent_platform.domain import ApprovalStatus, validate_id
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
        resource_ref=SourceRef(resource_type="approval", resource_id=approval.approval_id),
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


def approval_resolved_candidate(
    approval: ApprovalRecord,
    *,
    recipient: RecipientRef,
) -> NotificationCandidate:
    """Project #15's canonical resolved Approval state without owning that transition."""

    status = approval.status
    if status is ApprovalStatus.PENDING:
        raise ValueError("approval_resolved_candidate requires a resolved approval")
    severity = (
        NotificationSeverity.ERROR
        if status is ApprovalStatus.REJECTED
        else NotificationSeverity.WARNING
        if status in {ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED}
        else NotificationSeverity.INFO
    )
    return NotificationCandidate(
        category=NotificationCategory.APPROVAL,
        severity=severity,
        title="Approval resolved",
        summary={
            "approval_id": approval.approval_id,
            "status": status.value,
            "action": approval.action,
            "resource_type": approval.resource_type,
            "resource_id": approval.resource_id,
        },
        recipient=recipient,
        source=SourceRef(resource_type="approval", resource_id=approval.approval_id),
        project_id=approval.project_id,
        task_id=approval.task_id,
        run_id=approval.run_id,
        approval_id=approval.approval_id,
        resource_ref=SourceRef(resource_type="approval", resource_id=approval.approval_id),
        aggregation_key=f"approval:{approval.approval_id}:{status.value}",
    )


def verification_attention_candidate(
    *,
    verification_id: str,
    recipient: RecipientRef,
    attention: str,
    title: str,
    severity: NotificationSeverity = NotificationSeverity.WARNING,
    task_id: str | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> NotificationCandidate:
    """Project an opaque #86 Verification attention signal.

    `attention` is intentionally not an enum here: #86 owns Verification vocabulary/lifecycle.
    Its adapter passes the canonical signal through this seam.
    """

    validate_id(verification_id, "verification")
    if not attention.strip() or not title.strip():
        raise ValueError("verification attention/title must not be blank")
    return NotificationCandidate(
        category=NotificationCategory.VERIFICATION,
        severity=severity,
        title=title,
        summary={"attention": attention},
        recipient=recipient,
        source=SourceRef(resource_type="verification", resource_id=verification_id),
        project_id=project_id,
        task_id=task_id,
        run_id=run_id,
        verification_id=verification_id,
        resource_ref=SourceRef(resource_type="verification", resource_id=verification_id),
        aggregation_key=f"verification:{verification_id}:{attention}",
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def membership_attention_candidate(
    *,
    membership_id: str,
    recipient: RecipientRef,
    attention: str,
    title: str,
    severity: NotificationSeverity = NotificationSeverity.INFO,
    project_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> NotificationCandidate:
    """Project an opaque #87 membership/invitation attention signal.

    Recipient scope is already canonical (`user`, `team`, or `organization`). #87 remains the
    authority for whether that membership/invitation exists and who is currently eligible.
    """

    validate_id(membership_id, "membership")
    if not attention.strip() or not title.strip():
        raise ValueError("membership attention/title must not be blank")
    return NotificationCandidate(
        category=NotificationCategory.MEMBERSHIP,
        severity=severity,
        title=title,
        summary={"attention": attention},
        recipient=recipient,
        source=SourceRef(resource_type="membership", resource_id=membership_id),
        project_id=project_id,
        membership_id=membership_id,
        resource_ref=SourceRef(resource_type="membership", resource_id=membership_id),
        aggregation_key=f"membership:{membership_id}:{attention}",
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def canonical_attention_candidate(
    *,
    category: NotificationCategory,
    recipient: RecipientRef,
    source: SourceRef,
    attention: str,
    title: str,
    severity: NotificationSeverity = NotificationSeverity.WARNING,
    project_id: str | None = None,
    workspace_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    node_id: str | None = None,
    automation_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> NotificationCandidate:
    """Project a canonical attention signal without taking ownership of its source lifecycle.

    This is the narrow integration seam for canonical domains whose lifecycle vocabulary belongs
    elsewhere, including agent-input, Node/Worker, Automation, security and Connector events.
    The source domain supplies the canonical source reference, recipient and attention label.
    """

    if not attention.strip() or not title.strip():
        raise ValueError("canonical attention/title must not be blank")
    return NotificationCandidate(
        category=category,
        severity=severity,
        title=title,
        summary={"attention": attention},
        recipient=recipient,
        source=source,
        project_id=project_id,
        workspace_id=workspace_id,
        task_id=task_id,
        run_id=run_id,
        node_id=node_id,
        automation_id=automation_id,
        resource_ref=source,
        aggregation_key=(
            f"{category.value}:{source.resource_type}:{source.resource_id}:{attention}"
        ),
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def budget_threshold_candidate(
    event: BudgetThresholdEvent,
    *,
    recipient: RecipientRef,
    measurement_quality: str | None = None,
    threshold_generation: int | None = None,
) -> NotificationCandidate:
    """Project #76 threshold state without becoming the budget/accounting authority.

    `threshold_generation` distinguishes separate threshold episodes. The same persisted
    generation is reused during restart recovery, while a fresh crossing after recovery below
    threshold advances the generation and is therefore allowed to create new attention.
    """

    if threshold_generation is not None and threshold_generation < 1:
        raise ValueError("threshold_generation must be >= 1 when provided")
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
        "threshold_generation": threshold_generation,
    }
    legacy_key = f"budget:{event.budget_id}:{event.budget_version}:{event.level.value}"
    aggregation_key = (
        legacy_key
        if threshold_generation is None
        else (
            f"budget:{event.budget_id}:{event.budget_version}:"
            f"{threshold_generation}:{event.level.value}"
        )
    )
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
        aggregation_key=aggregation_key,
        causation_id=event.id,
    )
