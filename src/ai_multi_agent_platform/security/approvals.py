"""Exact-action approval records backed by the canonical domain Approval lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Approval, ApprovalStatus, OwnerRef, new_id

from .authorization import ProposedAction, RiskClassification


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """Security binding metadata around one canonical domain ``Approval`` entity."""

    approval: Approval
    requester_ref: str
    action: str
    resource_type: str
    resource_id: str
    requested_action_digest: str
    risk: RiskClassification
    policy_id: str
    expires_at: datetime
    task_id: str | None = None
    run_id: str | None = None
    capability_ref: str | None = None
    payload_ref: str | None = None
    decision_at: datetime | None = None
    decision_comment: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "requester_ref",
            "action",
            "resource_type",
            "resource_id",
            "requested_action_digest",
            "policy_id",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} must not be blank")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("expires_at must be timezone-aware")
        if self.expires_at <= self.approval.created_at:
            raise ValueError("expires_at must be after approval creation")
        if self.decision_at is not None and (
            self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None
        ):
            raise ValueError("decision_at must be timezone-aware")
        if self.decision_comment is not None and not self.decision_comment.strip():
            raise ValueError("decision_comment must not be blank when provided")

    @property
    def approval_id(self) -> str:
        return self.approval.id

    @property
    def status(self) -> ApprovalStatus:
        return self.approval.status

    @property
    def reason(self) -> str:
        return self.approval.reason

    @property
    def project_id(self) -> str | None:
        return self.approval.project_id

    @property
    def created_at(self) -> datetime:
        return self.approval.created_at

    @property
    def decision_by(self) -> OwnerRef | None:
        return self.approval.decision_by


class ApprovalService:
    """Reference approval lifecycle with exact-action digest binding.

    Mutation of an approval decision is intentionally restricted to the authorization
    gate. Application code must not be able to approve or cancel records by calling a
    storage/lifecycle primitive directly.
    """

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
        approval_id = new_id("approval")
        subject_type, subject_id = _approval_subject(action, approval_id)
        approval = Approval(
            id=approval_id,
            subject_type=subject_type,
            subject_id=subject_id,
            owner_ref=_owner_ref(action.context.actor.actor_id),
            reason=reason,
            project_id=action.context.operation.project_id,
            created_at=now,
            updated_at=now,
        )
        record = ApprovalRecord(
            approval=approval,
            requester_ref=action.context.actor.actor_id,
            action=action.context.action.value,
            resource_type=action.context.resource_type.value,
            resource_id=action.context.resource_id,
            requested_action_digest=action.digest,
            risk=risk,
            policy_id=policy_id,
            expires_at=expires_at or now + self._default_lifetime,
            task_id=action.context.task_id,
            run_id=action.context.run_id,
            capability_ref=action.context.capability_ref,
            payload_ref=action.payload_ref,
        )
        self._records[record.approval_id] = record
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        try:
            record = self._records[approval_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "approval was not found") from exc
        if record.status is ApprovalStatus.PENDING and record.expires_at <= datetime.now(UTC):
            now = datetime.now(UTC)
            record = replace(
                record,
                approval=replace(
                    record.approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                ),
                decision_at=now,
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
        del approval_id, approver_ref, approve, comment
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "approval decisions must be authorized through AuthorizationGate",
        )

    def cancel(self, approval_id: str, *, actor_ref: str) -> ApprovalRecord:
        del approval_id, actor_ref
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "approval cancellation must be authorized through AuthorizationGate",
        )

    def _decide_authorized(
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
        now = datetime.now(UTC)
        updated = replace(
            record,
            approval=replace(
                record.approval,
                status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
                decision_by=_owner_ref(approver_ref),
                updated_at=now,
            ),
            decision_at=now,
            decision_comment=comment,
        )
        self._records[approval_id] = updated
        return updated

    def _cancel_authorized(self, approval_id: str, *, actor_ref: str) -> ApprovalRecord:
        if not actor_ref.strip():
            raise ValueError("actor_ref must not be blank")
        record = self.get(approval_id)
        if record.status is not ApprovalStatus.PENDING:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"approval is not pending: {record.status.value}",
            )
        now = datetime.now(UTC)
        updated = replace(
            record,
            approval=replace(
                record.approval,
                status=ApprovalStatus.CANCELLED,
                decision_by=_owner_ref(actor_ref),
                updated_at=now,
            ),
            decision_at=now,
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


def _owner_ref(actor_ref: str) -> OwnerRef:
    prefix, separator, raw_id = actor_ref.partition(":")
    if separator and prefix in {"user", "organization", "team", "service"} and raw_id:
        return OwnerRef(type=prefix, id=raw_id)  # type: ignore[arg-type]
    return OwnerRef(type="service", id=actor_ref)


def _approval_subject(action: ProposedAction, approval_id: str) -> tuple[str, str]:
    if action.context.task_id is not None:
        return "task", action.context.task_id
    if action.context.run_id is not None:
        return "run", action.context.run_id
    return "approval", approval_id
