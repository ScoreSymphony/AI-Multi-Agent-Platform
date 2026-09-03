"""Exact-action approval records and deterministic in-memory reference service."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import ApprovalStatus, new_id, validate_id

from .authorization import ProposedAction, RiskClassification


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    approval_id: str
    requester_ref: str
    action: str
    resource_type: str
    resource_id: str
    requested_action_digest: str
    reason: str
    risk: RiskClassification
    policy_id: str
    created_at: datetime
    expires_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    project_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    capability_ref: str | None = None
    payload_ref: str | None = None
    decision_by: str | None = None
    decision_at: datetime | None = None
    decision_comment: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.approval_id, "approval")
        for name in (
            "requester_ref",
            "action",
            "resource_type",
            "resource_id",
            "requested_action_digest",
            "reason",
            "policy_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.decision_at is not None and (
            self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None
        ):
            raise ValueError("decision_at must be timezone-aware")
        if self.decision_comment is not None and not self.decision_comment.strip():
            raise ValueError("decision_comment must not be blank when provided")


class ApprovalService:
    """Reference approval lifecycle with exact-action digest binding."""

    def __init__(self, *, default_lifetime: timedelta = timedelta(minutes=15)) -> None:
        if default_lifetime <= timedelta(0):
            raise ValueError("default approval lifetime must be positive")
        self._default_lifetime = default_lifetime
        self._records: dict[str, ApprovalRecord] = {}

    def request(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
        expires_at: datetime | None = None,
    ) -> ApprovalRecord:
        if not reason.strip():
            raise ValueError("approval reason must not be blank")
        if not policy_id.strip():
            raise ValueError("approval policy_id must not be blank")
        existing = self.pending_for(action)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        record = ApprovalRecord(
            approval_id=new_id("approval"),
            requester_ref=action.context.actor.actor_id,
            action=action.context.action.value,
            resource_type=action.context.resource_type.value,
            resource_id=action.context.resource_id,
            requested_action_digest=action.digest,
            reason=reason,
            risk=risk,
            policy_id=policy_id,
            created_at=now,
            expires_at=expires_at or now + self._default_lifetime,
            project_id=action.context.operation.project_id,
            task_id=action.context.task_id,
            run_id=action.context.run_id,
            capability_ref=action.context.capability_ref,
            payload_ref=action.payload_ref,
        )
        self._records[record.approval_id] = record
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        validate_id(approval_id, "approval")
        try:
            record = self._records[approval_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "approval was not found") from exc
        if record.status is ApprovalStatus.PENDING and record.expires_at <= datetime.now(UTC):
            record = replace(
                record,
                status=ApprovalStatus.EXPIRED,
                decision_at=datetime.now(UTC),
            )
            self._records[approval_id] = record
        return record

    def decide(
        self,
        approval_id: str,
        *,
        approver_ref: str,
        approve: bool,
        comment: str | None = None,
    ) -> ApprovalRecord:
        if not approver_ref.strip():
            raise ValueError("approver_ref must not be blank")
        record = self.get(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"approval is not pending: {record.status.value}",
            )
        updated = replace(
            record,
            status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
            decision_by=approver_ref,
            decision_at=datetime.now(UTC),
            decision_comment=comment,
        )
        self._records[approval_id] = updated
        return updated

    def cancel(self, approval_id: str, *, actor_ref: str) -> ApprovalRecord:
        if not actor_ref.strip():
            raise ValueError("actor_ref must not be blank")
        record = self.get(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"approval is not pending: {record.status.value}",
            )
        updated = replace(
            record,
            status=ApprovalStatus.CANCELLED,
            decision_by=actor_ref,
            decision_at=datetime.now(UTC),
        )
        self._records[approval_id] = updated
        return updated

    def valid_for(self, approval_id: str, action: ProposedAction) -> bool:
        record = self.get(approval_id)
        return (
            record.status is ApprovalStatus.APPROVED
            and record.expires_at > datetime.now(UTC)
            and record.requested_action_digest == action.digest
        )

    def find_valid_for(self, action: ProposedAction) -> ApprovalRecord | None:
        for approval_id in tuple(self._records):
            record = self.get(approval_id)
            if (
                record.status is ApprovalStatus.APPROVED
                and record.expires_at > datetime.now(UTC)
                and record.requested_action_digest == action.digest
            ):
                return record
        return None

    def pending_for(self, action: ProposedAction) -> ApprovalRecord | None:
        for approval_id in tuple(self._records):
            record = self.get(approval_id)
            if (
                record.status is ApprovalStatus.PENDING
                and record.requested_action_digest == action.digest
            ):
                return record
        return None

    def all(self) -> tuple[ApprovalRecord, ...]:
        for approval_id in tuple(self._records):
            self.get(approval_id)
        return tuple(self._records.values())
