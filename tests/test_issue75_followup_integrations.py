from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.domain import Approval, ApprovalStatus, OwnerRef, new_id
from ai_multi_agent_platform.notifications import (
    InMemoryNotificationPreferenceRepository,
    InMemoryNotificationRepository,
    NotificationCandidate,
    NotificationCategory,
    NotificationQuery,
    NotificationService,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
    approval_resolved_candidate,
    membership_attention_candidate,
    verification_attention_candidate,
)
from ai_multi_agent_platform.security.approvals import ApprovalRecord
from ai_multi_agent_platform.security.authorization import RiskClassification


def _recipient(kind: RecipientType = RecipientType.USER) -> RecipientRef:
    return RecipientRef(kind, new_id(kind.value))


def _service(*, recipient_eligibility=None) -> NotificationService:
    return NotificationService(
        repository=InMemoryNotificationRepository(),
        preferences=InMemoryNotificationPreferenceRepository(),
        recipient_eligibility=recipient_eligibility,
    )


def test_verification_required_and_changes_requested_use_opaque_issue86_attention_contract() -> (
    None
):
    recipient = _recipient()
    verification_id = new_id("verification")
    task_id = new_id("task")

    required = verification_attention_candidate(
        verification_id=verification_id,
        recipient=recipient,
        attention="required",
        title="Verification required",
        task_id=task_id,
    )
    changes = verification_attention_candidate(
        verification_id=verification_id,
        recipient=recipient,
        attention="changes_requested",
        title="Verification changes requested",
        severity=NotificationSeverity.ERROR,
        task_id=task_id,
    )

    assert required.category is NotificationCategory.VERIFICATION
    assert required.verification_id == verification_id
    assert required.source == SourceRef("verification", verification_id)
    assert required.summary == {"attention": "required"}
    assert changes.summary == {"attention": "changes_requested"}
    assert changes.severity is NotificationSeverity.ERROR
    assert changes.aggregation_key != required.aggregation_key


def test_membership_attention_supports_canonical_organization_scope_without_owning_issue87() -> (
    None
):
    organization = _recipient(RecipientType.ORGANIZATION)
    membership_id = new_id("membership")

    candidate = membership_attention_candidate(
        membership_id=membership_id,
        recipient=organization,
        attention="invitation_pending",
        title="Organization invitation",
    )

    assert candidate.category is NotificationCategory.MEMBERSHIP
    assert candidate.recipient == organization
    assert candidate.membership_id == membership_id
    assert candidate.source == SourceRef("membership", membership_id)
    assert candidate.summary == {"attention": "invitation_pending"}


def test_recipient_eligibility_stops_new_notifications_but_preserves_history() -> None:
    class MutableEligibility:
        def __init__(self) -> None:
            self.allowed = True

        async def allows(self, candidate: NotificationCandidate) -> bool:
            del candidate
            return self.allowed

    async def scenario() -> None:
        recipient = _recipient()
        guard = MutableEligibility()
        service = _service(recipient_eligibility=guard)

        first = NotificationCandidate(
            category=NotificationCategory.MEMBERSHIP,
            severity=NotificationSeverity.INFO,
            title="Membership active",
            summary={"attention": "active"},
            recipient=recipient,
            source=SourceRef("membership", new_id("membership")),
        )
        created = await service.create(first)
        assert created is not None

        guard.allowed = False
        denied = await service.create(
            NotificationCandidate(
                category=NotificationCategory.MEMBERSHIP,
                severity=NotificationSeverity.WARNING,
                title="Should not be delivered",
                summary={"attention": "removed"},
                recipient=recipient,
                source=SourceRef("membership", new_id("membership")),
            )
        )
        assert denied is None

        history = await service.list(NotificationQuery(recipient=recipient))
        assert tuple(item.id for item in history) == (created.id,)
        assert await service.unread_count(recipient) == 1

    asyncio.run(scenario())


def test_approval_resolved_projection_uses_canonical_issue15_status_without_payload() -> None:
    now = datetime(2026, 9, 4, tzinfo=UTC)
    recipient = _recipient()
    task_id = new_id("task")
    approval = Approval(
        subject_type="task",
        subject_id=task_id,
        owner_ref=OwnerRef(type="user", id=recipient.id),
        reason="governed action",
        status=ApprovalStatus.APPROVED,
        created_at=now,
        updated_at=now,
    )
    record = ApprovalRecord(
        approval=approval,
        requester_ref=recipient.id,
        action="execute",
        resource_type="task",
        resource_id=task_id,
        requested_action_digest="digest",
        risk=RiskClassification.ELEVATED,
        policy_id="policy-75",
        expires_at=now + timedelta(minutes=15),
        task_id=task_id,
        payload_ref="private-payload-reference",
        decision_at=now,
    )

    candidate = approval_resolved_candidate(record, recipient=recipient)

    assert candidate.category is NotificationCategory.APPROVAL
    assert candidate.summary["status"] == "approved"
    assert candidate.severity is NotificationSeverity.INFO
    assert candidate.approval_id == approval.id
    assert candidate.source == SourceRef("approval", approval.id)
    assert "payload_ref" not in candidate.summary
    assert "requested_action_digest" not in candidate.summary
