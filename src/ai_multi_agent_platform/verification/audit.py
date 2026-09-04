"""Canonical, content-safe audit records for runtime Verification (#86)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id

from .models import VerificationOutcome, VerificationSubject, VerifierIdentity, VerifierKind


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _optional_nonblank(value: str | None, field_name: str) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name} must not be blank when provided")


class VerificationAuditEventType(StrEnum):
    """Stable event names for append-only Verification audit history."""

    POLICY_REGISTERED = "verification.policy_registered"
    REQUESTED = "verification.requested"
    REVERIFICATION_REQUESTED = "verification.reverification_requested"
    REQUEST_EXPIRED = "verification.request_expired"
    RESULT_RECORDED = "verification.result_recorded"


@dataclass(frozen=True, slots=True)
class VerificationAuditEvent:
    """One immutable Verification audit fact.

    The record deliberately contains references, digests and classifications rather than
    review comments, Artifact bodies or other potentially sensitive content. Human/Agent
    findings remain available through the canonical VerificationResult itself.
    """

    event_type: VerificationAuditEventType
    event_id: str = field(default_factory=lambda: new_id("verification_audit"))
    occurred_at: datetime = field(default_factory=_utc_now)
    task_id: str | None = None
    verification_id: str | None = None
    run_id: str | None = None
    project_id: str | None = None
    policy_id: str | None = None
    policy_version: int | None = None
    stage_id: str | None = None
    subject: VerificationSubject | None = None
    requested_verifier_kind: VerifierKind | None = None
    verifier: VerifierIdentity | None = None
    outcome: VerificationOutcome | None = None
    repair_attempt: int = 0
    correlation_id: str | None = None
    causation_id: str | None = None
    evidence_artifact_ids: tuple[str, ...] = ()
    checks_executed: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.event_id, "verification_audit")
        _require_aware(self.occurred_at, "verification audit occurred_at")
        if self.task_id is not None:
            validate_id(self.task_id, "task")
        if self.verification_id is not None:
            validate_id(self.verification_id, "verification")
        if self.run_id is not None:
            validate_id(self.run_id, "run")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.policy_id is not None:
            validate_id(self.policy_id, "verification_policy")
            if self.policy_version is None or self.policy_version < 1:
                raise ValueError("verification audit policy requires version >= 1")
        elif self.policy_version is not None:
            raise ValueError("verification audit policy_version requires policy_id")
        _optional_nonblank(self.stage_id, "verification audit stage_id")
        _optional_nonblank(self.correlation_id, "verification audit correlation_id")
        _optional_nonblank(self.causation_id, "verification audit causation_id")
        if self.repair_attempt < 0:
            raise ValueError("verification audit repair_attempt must be >= 0")
        for artifact_id in self.evidence_artifact_ids:
            validate_id(artifact_id, "artifact")
        for check in self.checks_executed:
            if not check.strip():
                raise ValueError("verification audit checks_executed must not contain blanks")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
