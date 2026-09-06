"""Provider-neutral durable Plan/Step coordination models for issue #384.

The public lifecycle remains the canonical :mod:`ai_multi_agent_platform.domain` model.
These records contain only runtime coordination state that is not already represented by
Task/Plan/Step/Run/Event entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, validate_id


class CoordinationPhase(StrEnum):
    BLOCKED = "blocked"
    READY = "ready"
    ATTEMPT_ACTIVE = "attempt_active"
    WAITING = "waiting"
    RETRY_SCHEDULED = "retry_scheduled"
    TERMINAL = "terminal"
    INCONSISTENT = "inconsistent"


class WaitType(StrEnum):
    DEADLINE = "deadline"
    APPROVAL = "approval"
    EVENT = "event"
    EXTERNAL_JOB = "external_job"


class WaitResolution(StrEnum):
    SATISFIED = "satisfied"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class PredecessorFailurePolicy(StrEnum):
    FAIL_FAST = "fail_fast"
    CANCEL_DEPENDENT = "cancel_dependent"


class ReconciliationDisposition(StrEnum):
    CONSISTENT = "consistent"
    RUN_RECONCILED = "run_reconciled"
    WAIT_RESUMED = "wait_resumed"
    RETRY_RESUMED = "retry_resumed"
    CANONICAL_TERMINAL = "canonical_terminal"
    MISSING_CANONICAL_RUN = "missing_canonical_run"
    INCONSISTENT = "inconsistent"


@dataclass(frozen=True, slots=True)
class StepRetryPolicy:
    """Versioned Step-level retry policy, independent from transport/executor retries."""

    max_attempts: int = 1
    initial_delay_seconds: float = 1.0
    multiplier: float = 2.0
    max_delay_seconds: float = 300.0
    retryable_categories: tuple[str, ...] = ()
    version: int = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.initial_delay_seconds < 0:
            raise ValueError("initial_delay_seconds must be >= 0")
        if self.multiplier < 1:
            raise ValueError("multiplier must be >= 1")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be >= 0")
        if self.version < 1:
            raise ValueError("retry policy version must be >= 1")
        if len(self.retryable_categories) != len(set(self.retryable_categories)):
            raise ValueError("retryable failure categories must be unique")
        if any(not item.strip() for item in self.retryable_categories):
            raise ValueError("retryable failure categories must not be blank")

    def delay_for_attempt(self, next_attempt: int) -> timedelta:
        if next_attempt < 2:
            raise ValueError("next_attempt must be >= 2")
        exponent = next_attempt - 2
        seconds = min(
            self.max_delay_seconds,
            self.initial_delay_seconds * (self.multiplier**exponent),
        )
        return timedelta(seconds=seconds)

    def permits(self, *, category: str | None, next_attempt: int) -> bool:
        if next_attempt > self.max_attempts or category is None:
            return False
        return category in self.retryable_categories


@dataclass(frozen=True, slots=True)
class StepWait:
    """Safe canonical wait descriptor without raw provider/webhook payloads."""

    wait_key: str
    wait_type: WaitType
    task_id: str
    plan_id: str
    step_id: str
    owner_ref: OwnerRef
    project_id: str | None = None
    deadline_at: datetime | None = None
    approval_id: str | None = None
    approval_subject_type: str | None = None
    approval_subject_id: str | None = None
    approval_action: str | None = None
    event_type: str | None = None
    correlation_key: str | None = None
    external_job_ref: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None
    resolution: WaitResolution | None = None
    resolution_key: str | None = None

    def __post_init__(self) -> None:
        if not self.wait_key.strip():
            raise ValueError("wait_key must not be blank")
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.step_id, "step")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.deadline_at is not None and self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must be timezone-aware")
        if self.wait_type is WaitType.DEADLINE and self.deadline_at is None:
            raise ValueError("deadline wait requires deadline_at")
        if self.wait_type is WaitType.APPROVAL:
            required = (
                self.approval_id,
                self.approval_subject_type,
                self.approval_subject_id,
                self.approval_action,
            )
            if any(item is None or not item.strip() for item in required):
                raise ValueError("approval wait requires approval identity, subject and action")
        if self.wait_type is WaitType.EVENT:
            if not self.event_type or not self.event_type.strip():
                raise ValueError("event wait requires event_type")
            if not self.correlation_key or not self.correlation_key.strip():
                raise ValueError("event wait requires correlation_key")
        if self.wait_type is WaitType.EXTERNAL_JOB:
            if not self.external_job_ref or not self.external_job_ref.strip():
                raise ValueError("external job wait requires canonical adapter reference")

    @property
    def resolved(self) -> bool:
        return self.resolved_at is not None


@dataclass(frozen=True, slots=True)
class StepCoordinationRecord:
    task_id: str
    plan_id: str
    plan_revision: int
    step_id: str
    phase: CoordinationPhase
    dependency_ids: tuple[str, ...] = ()
    satisfied_dependency_ids: tuple[str, ...] = ()
    latest_run_id: str | None = None
    current_attempt: int = 0
    retry_policy: StepRetryPolicy = field(default_factory=StepRetryPolicy)
    retry_due_at: datetime | None = None
    wait: StepWait | None = None
    predecessor_failure_policy: PredecessorFailurePolicy = PredecessorFailurePolicy.FAIL_FAST
    processed_keys: tuple[str, ...] = ()
    reconciliation: ReconciliationDisposition = ReconciliationDisposition.CONSISTENT
    reconciliation_detail: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    provenance_source: str = "platform-coordinator"
    revision: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.step_id, "step")
        for dependency_id in self.dependency_ids:
            validate_id(dependency_id, "step")
        for dependency_id in self.satisfied_dependency_ids:
            validate_id(dependency_id, "step")
        if self.latest_run_id is not None:
            validate_id(self.latest_run_id, "run")
        if self.plan_revision < 1 or self.revision < 1:
            raise ValueError("plan/coordination revisions must be >= 1")
        if self.current_attempt < 0:
            raise ValueError("current_attempt must be >= 0")
        if self.retry_due_at is not None and self.retry_due_at.tzinfo is None:
            raise ValueError("retry_due_at must be timezone-aware")
        if len(self.dependency_ids) != len(set(self.dependency_ids)):
            raise ValueError("dependency IDs must be unique")
        if len(self.satisfied_dependency_ids) != len(set(self.satisfied_dependency_ids)):
            raise ValueError("satisfied dependency IDs must be unique")
        if not set(self.satisfied_dependency_ids).issubset(self.dependency_ids):
            raise ValueError("satisfied dependencies must be declared dependencies")
        if len(self.processed_keys) != len(set(self.processed_keys)):
            raise ValueError("processed coordination keys must be unique")


@dataclass(frozen=True, slots=True)
class PlanRuntimeState:
    """Canonical task-bound Plan/Step snapshot owned by the platform coordinator store."""

    plan: Plan
    steps: tuple[Step, ...]
    store_revision: int = 1

    def __post_init__(self) -> None:
        if self.store_revision < 1:
            raise ValueError("store_revision must be >= 1")
        if not self.plan.active:
            raise ValueError("coordinator accepts only the active canonical Plan")
        ids = tuple(step.id for step in self.steps)
        if len(ids) != len(set(ids)):
            raise ValueError("step IDs must be unique")
        if any(step.plan_id != self.plan.id for step in self.steps):
            raise ValueError("every Step must belong to the active Plan")

    def step(self, step_id: str) -> Step:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise KeyError(step_id)


@dataclass(frozen=True, slots=True)
class CoordinatorClaim:
    step_id: str
    claim_id: str
    owner_id: str
    fence: int
    expires_at: datetime

    def __post_init__(self) -> None:
        validate_id(self.step_id, "step")
        if not self.claim_id.strip() or not self.owner_id.strip():
            raise ValueError("claim identity and owner must not be blank")
        if self.fence < 1:
            raise ValueError("claim fence must be >= 1")
        if self.expires_at.tzinfo is None:
            raise ValueError("claim expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StepCoordinationProjection:
    step_id: str
    status: StepStatus
    phase: CoordinationPhase
    dependency_ids: tuple[str, ...]
    satisfied_dependency_ids: tuple[str, ...]
    latest_run_id: str | None
    current_attempt: int
    retry_due_at: datetime | None
    wait_type: WaitType | None
    wait_deadline_at: datetime | None
    reconciliation: ReconciliationDisposition


@dataclass(frozen=True, slots=True)
class PlanCoordinationProjection:
    task_id: str
    plan_id: str
    plan_revision: int
    steps: tuple[StepCoordinationProjection, ...]


ApprovalOutcome = Literal["approved", "rejected", "expired", "cancelled"]
