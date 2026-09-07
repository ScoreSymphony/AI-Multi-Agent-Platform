"""Canonical runtime verification models for issue #86.

Verification is deliberately separate from security Approval and regression Evaluation.
These immutable records bind review outcomes to one exact Result/Artifact revision and
content digest so a changed output can never inherit an older verification implicitly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance, new_id, validate_id
from ai_multi_agent_platform.security.authorization import RiskClassification


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _validate_optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


def _freeze_metadata(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


class VerifierKind(StrEnum):
    DETERMINISTIC = "deterministic"
    HUMAN = "human"
    AGENT = "agent"
    PROVIDER = "provider"


class VerificationOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NEEDS_CHANGES = "needs_changes"
    INCONCLUSIVE = "inconclusive"


class VerificationRequestStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class VerificationFailurePolicy(StrEnum):
    FAIL = "fail"
    ESCALATE = "escalate"
    WAIT = "wait"


class CompletionState(StrEnum):
    ACCEPTED = "accepted"
    WAITING = "waiting"
    REPAIR_REQUIRED = "repair_required"
    REJECTED = "rejected"
    ESCALATED = "escalated"


@dataclass(frozen=True, slots=True)
class VerificationSubject:
    """Exact immutable subject snapshot reviewed by one verification request."""

    subject_type: str
    subject_id: str
    revision: str
    digest: str

    def __post_init__(self) -> None:
        if self.subject_type not in {"result", "artifact"}:
            raise ValueError("verification subject_type must be result or artifact")
        validate_id(self.subject_id, self.subject_type)
        _require_nonblank(self.revision, "verification subject revision")
        _require_nonblank(self.digest, "verification subject digest")


@dataclass(frozen=True, slots=True)
class VerificationScope:
    """Optional applicability restrictions for a reusable verification policy."""

    task_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    agent_ids: tuple[str, ...] = ()
    capability_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for task_id in self.task_ids:
            validate_id(task_id, "task")
        for project_id in self.project_ids:
            validate_id(project_id, "project")
        for agent_id in self.agent_ids:
            validate_id(agent_id, "agent")
        for capability_id in self.capability_ids:
            _require_nonblank(capability_id, "verification policy capability ID")
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("verification policy task scopes must be unique")
        if len(set(self.project_ids)) != len(self.project_ids):
            raise ValueError("verification policy project scopes must be unique")
        if len(set(self.agent_ids)) != len(self.agent_ids):
            raise ValueError("verification policy agent scopes must be unique")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("verification policy capability scopes must be unique")


@dataclass(frozen=True, slots=True)
class ReviewerIndependence:
    """Policy-selectable independence constraints; none are mandatory globally."""

    producer_agent_must_differ: bool = False
    model_must_differ: bool = False
    provider_must_differ: bool = False
    agent_reviewer_must_be_read_only: bool = False
    human_reviewer_must_differ: bool = False
    forbid_self_verification: bool = False
    forbid_self_verification_risk_classes: tuple[RiskClassification, ...] = ()
    require_distinct_verifiers: bool = False

    def __post_init__(self) -> None:
        if len(set(self.forbid_self_verification_risk_classes)) != len(
            self.forbid_self_verification_risk_classes
        ):
            raise ValueError("self-verification risk classes must be unique")


@dataclass(frozen=True, slots=True)
class VerificationStage:
    stage_id: str
    verifier_kind: VerifierKind
    minimum_results: int = 1
    accepted_outcomes: tuple[VerificationOutcome, ...] = (VerificationOutcome.PASS,)
    capability_ref: str | None = None
    critical: bool = True

    def __post_init__(self) -> None:
        _require_nonblank(self.stage_id, "verification stage_id")
        if self.minimum_results < 1:
            raise ValueError("verification stage minimum_results must be >= 1")
        if not self.accepted_outcomes:
            raise ValueError("verification stage requires at least one accepted outcome")
        if len(set(self.accepted_outcomes)) != len(self.accepted_outcomes):
            raise ValueError("verification stage accepted outcomes must be unique")
        if self.capability_ref is not None:
            _require_nonblank(self.capability_ref, "verification capability_ref")


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Versioned platform-owned policy deciding which runtime reviews are required."""

    name: str
    stages: tuple[VerificationStage, ...]
    policy_id: str = field(default_factory=lambda: new_id("verification_policy"))
    version: int = 1
    scope: VerificationScope = VerificationScope()
    independence: ReviewerIndependence = ReviewerIndependence()
    risk_classification: RiskClassification = RiskClassification.STANDARD
    max_repair_attempts: int = 0
    request_timeout_seconds: float | None = None
    result_expiry_seconds: float | None = None
    failure_policy: VerificationFailurePolicy = VerificationFailurePolicy.FAIL
    timeout_failure_policy: VerificationFailurePolicy = VerificationFailurePolicy.WAIT
    creator_ref: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.policy_id, "verification_policy")
        _require_nonblank(self.name, "verification policy name")
        if self.version < 1:
            raise ValueError("verification policy version must be >= 1")
        stage_ids = [stage.stage_id for stage in self.stages]
        if len(set(stage_ids)) != len(stage_ids):
            raise ValueError("verification policy stage IDs must be unique")
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")
        if self.request_timeout_seconds is not None and self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")
        if self.result_expiry_seconds is not None and self.result_expiry_seconds <= 0:
            raise ValueError("result_expiry_seconds must be > 0")
        if self.creator_ref is not None:
            _require_nonblank(self.creator_ref, "verification policy creator_ref")
        _require_aware(self.created_at, "verification policy created_at")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    def stage(self, stage_id: str) -> VerificationStage:
        for stage in self.stages:
            if stage.stage_id == stage_id:
                return stage
        raise KeyError(stage_id)


@dataclass(frozen=True, slots=True)
class ProducerIdentity:
    """Producer provenance used only for optional reviewer-independence enforcement."""

    actor_ref: str
    agent_id: str | None = None
    agent_revision: int | None = None
    model_config_id: str | None = None
    provider_id: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.actor_ref, "producer actor_ref")
        if self.agent_id is not None:
            validate_id(self.agent_id, "agent")
            if self.agent_revision is None or self.agent_revision < 1:
                raise ValueError("producer agent identity requires agent_revision >= 1")
        elif self.agent_revision is not None:
            raise ValueError("producer agent_revision requires agent_id")
        if self.model_config_id is not None:
            _require_nonblank(self.model_config_id, "producer model_config_id")
        if self.provider_id is not None:
            _require_nonblank(self.provider_id, "producer provider_id")


@dataclass(frozen=True, slots=True)
class VerifierIdentity:
    verifier_ref: str
    kind: VerifierKind
    agent_id: str | None = None
    agent_revision: int | None = None
    model_config_id: str | None = None
    provider_id: str | None = None
    read_only: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.verifier_ref, "verifier_ref")
        if self.kind is VerifierKind.AGENT:
            if self.agent_id is None or self.agent_revision is None:
                raise ValueError("agent verifier requires agent_id and agent_revision")
            validate_id(self.agent_id, "agent")
            if self.agent_revision < 1:
                raise ValueError("agent verifier revision must be >= 1")
        elif self.agent_id is not None or self.agent_revision is not None:
            raise ValueError("only agent verifiers may carry agent revision identity")
        if self.model_config_id is not None:
            _require_nonblank(self.model_config_id, "verifier model_config_id")
        if self.provider_id is not None:
            _require_nonblank(self.provider_id, "verifier provider_id")


@dataclass(frozen=True, slots=True)
class VerificationRequest:
    task_id: str
    policy_id: str
    policy_version: int
    stage_id: str
    subject: VerificationSubject
    requested_verifier_kind: VerifierKind
    correlation_id: str
    verification_id: str = field(default_factory=lambda: new_id("verification"))
    run_id: str | None = None
    result_id: str | None = None
    artifact_ids: tuple[str, ...] = ()
    project_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    requested_capability_ref: str | None = None
    producer: ProducerIdentity | None = None
    repair_attempt: int = 0
    status: VerificationRequestStatus = VerificationRequestStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.verification_id, "verification")
        validate_id(self.task_id, "task")
        validate_id(self.policy_id, "verification_policy")
        _validate_optional_id(self.run_id, "run")
        _validate_optional_id(self.result_id, "result")
        _validate_optional_id(self.project_id, "project")
        for capability_id in self.capability_ids:
            _require_nonblank(capability_id, "verification capability ID")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("verification capability references must be unique")
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")
        if len(set(self.artifact_ids)) != len(self.artifact_ids):
            raise ValueError("verification artifact references must be unique")
        if self.policy_version < 1:
            raise ValueError("verification policy_version must be >= 1")
        _require_nonblank(self.stage_id, "verification stage_id")
        _require_nonblank(self.correlation_id, "verification correlation_id")
        if self.requested_capability_ref is not None:
            _require_nonblank(self.requested_capability_ref, "requested_capability_ref")
        if self.repair_attempt < 0:
            raise ValueError("repair_attempt must be >= 0")
        _require_aware(self.created_at, "verification created_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "verification expires_at")
            if self.expires_at <= self.created_at:
                raise ValueError("verification expires_at must follow created_at")
        if self.subject.subject_type == "result" and self.result_id != self.subject.subject_id:
            raise ValueError("result subject must match result_id")
        if (
            self.subject.subject_type == "artifact"
            and self.subject.subject_id not in self.artifact_ids
        ):
            raise ValueError("artifact subject must be present in artifact_ids")


@dataclass(frozen=True, slots=True)
class VerificationFinding:
    code: str
    message: str
    severity: str = "info"
    location_ref: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.code, "verification finding code")
        _require_nonblank(self.message, "verification finding message")
        _require_nonblank(self.severity, "verification finding severity")
        if self.location_ref is not None:
            _require_nonblank(self.location_ref, "verification finding location_ref")


@dataclass(frozen=True, slots=True)
class VerificationError:
    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.code, "verification error code")
        _require_nonblank(self.message, "verification error message")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verification_id: str
    verifier: VerifierIdentity
    outcome: VerificationOutcome
    subject: VerificationSubject
    verification_result_id: str = field(default_factory=lambda: new_id("verification_result"))
    findings: tuple[VerificationFinding, ...] = ()
    evidence_artifact_ids: tuple[str, ...] = ()
    checks_executed: tuple[str, ...] = ()
    errors: tuple[VerificationError, ...] = ()
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.verification_result_id, "verification_result")
        validate_id(self.verification_id, "verification")
        for artifact_id in self.evidence_artifact_ids:
            validate_id(artifact_id, "artifact")
        if len(set(self.evidence_artifact_ids)) != len(self.evidence_artifact_ids):
            raise ValueError("verification evidence artifact IDs must be unique")
        for check in self.checks_executed:
            _require_nonblank(check, "verification check name")
        if len(set(self.checks_executed)) != len(self.checks_executed):
            raise ValueError("verification checks_executed must be unique")
        _require_aware(self.started_at, "verification started_at")
        _require_aware(self.completed_at, "verification completed_at")
        if self.completed_at < self.started_at:
            raise ValueError("verification completed_at cannot precede started_at")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    task_id: str
    subject: VerificationSubject
    state: CompletionState
    reason: str
    policy_id: str | None = None
    policy_version: int | None = None
    blocking_verification_ids: tuple[str, ...] = ()
    repair_attempts_remaining: int = 0

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        _require_nonblank(self.reason, "completion assessment reason")
        if self.policy_id is not None:
            validate_id(self.policy_id, "verification_policy")
            if self.policy_version is None or self.policy_version < 1:
                raise ValueError("completion assessment policy requires version >= 1")
        elif self.policy_version is not None:
            raise ValueError("policy_version requires policy_id")
        for verification_id in self.blocking_verification_ids:
            validate_id(verification_id, "verification")
        if self.repair_attempts_remaining < 0:
            raise ValueError("repair_attempts_remaining must be >= 0")
