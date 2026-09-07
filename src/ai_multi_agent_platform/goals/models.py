"""Canonical durable Goal models for continuous objective management."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Goal, OwnerRef, Provenance, new_id, utc_now, validate_id

CriterionOperator = Literal["eq", "gte", "lte", "gt", "lt", "truthy"]
TaskRevisionPolicy = Literal["retain", "supersede"]


class GoalStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    WAITING = "waiting"
    PAUSED = "paused"
    SATISFIED = "satisfied"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


class GoalProgress(StrEnum):
    UNKNOWN = "unknown"
    MONITORING = "monitoring"
    ACTIVE_WORK = "active_work"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    SATISFIED = "satisfied"
    DEGRADED = "degraded"


class GoalCriterionKind(StrEnum):
    HUMAN_ACCEPTANCE = "human_acceptance"
    METRIC = "metric"
    CANONICAL_STATE = "canonical_state"
    VERIFIED_ASSERTION = "verified_assertion"
    LINKED_TASKS = "linked_tasks"


class GoalCriterionState(StrEnum):
    UNKNOWN = "unknown"
    UNSATISFIED = "unsatisfied"
    SATISFIED = "satisfied"


class GoalTaskState(StrEnum):
    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True, kw_only=True)
class SuccessCriterion:
    criterion_id: str
    kind: GoalCriterionKind
    description: str
    operator: CriterionOperator = "truthy"
    target: JsonValue = True
    required: bool = True

    def __post_init__(self) -> None:
        if not self.criterion_id.strip():
            raise ValueError("criterion_id must not be blank")
        if not self.description.strip():
            raise ValueError("criterion description must not be blank")
        if self.kind is GoalCriterionKind.METRIC and self.operator == "truthy":
            raise ValueError("metric criteria require an explicit comparison operator")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoalEvidence:
    criterion_id: str
    kind: str
    value: JsonValue
    source_ref: str
    verified: bool
    evidence_id: str = field(default_factory=lambda: new_id("event"))
    actor_ref: str | None = None
    observed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.evidence_id, "event")
        if not self.criterion_id.strip():
            raise ValueError("evidence criterion_id must not be blank")
        if not self.kind.strip():
            raise ValueError("evidence kind must not be blank")
        if not self.source_ref.strip():
            raise ValueError("evidence source_ref must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoalConstraints:
    requirements: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    risk_requirements: tuple[str, ...] = ()
    data_requirements: tuple[str, ...] = ()
    security_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for collection in (
            self.requirements,
            self.out_of_scope,
            self.risk_requirements,
            self.data_requirements,
            self.security_requirements,
        ):
            if any(not item.strip() for item in collection):
                raise ValueError("goal constraints must not contain blank entries")


@dataclass(frozen=True, slots=True, kw_only=True)
class ObservationPolicy:
    automation_id: str | None = None
    review_interval_seconds: int | None = None
    event_types: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.automation_id is not None and not self.automation_id.strip():
            raise ValueError("automation_id must not be blank")
        if self.review_interval_seconds is not None and self.review_interval_seconds <= 0:
            raise ValueError("review_interval_seconds must be greater than zero")
        if any(not event_type.strip() for event_type in self.event_types):
            raise ValueError("observation event types must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskGenerationPolicy:
    enabled: bool = True
    task_title: str | None = None
    proposal_required: bool = False

    def __post_init__(self) -> None:
        if self.task_title is not None and not self.task_title.strip():
            raise ValueError("task_title must not be blank")


@dataclass(frozen=True, slots=True, kw_only=True)
class AutonomyPolicy:
    max_tasks_per_review: int = 1
    max_consecutive_failed_cycles: int = 3
    human_checkpoint_required: bool = False

    def __post_init__(self) -> None:
        if self.max_tasks_per_review < 0:
            raise ValueError("max_tasks_per_review must be >= 0")
        if self.max_consecutive_failed_cycles < 1:
            raise ValueError("max_consecutive_failed_cycles must be >= 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class CriterionEvaluation:
    criterion_id: str
    state: GoalCriterionState
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class GoalTaskLink:
    task_id: str
    goal_revision: int
    review_id: str | None
    task_state: GoalTaskState = GoalTaskState.ACTIVE
    valid_for_current_revision: bool = True
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.task_id, "task")
        if self.goal_revision < 1:
            raise ValueError("goal_revision must be >= 1")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoalReview:
    review_id: str
    goal_revision: int
    trigger_ref: str
    criterion_evaluations: tuple[CriterionEvaluation, ...]
    generated_task_ids: tuple[str, ...] = ()
    work_required: bool = False
    decision_reason: str = ""
    reviewed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.review_id.strip():
            raise ValueError("review_id must not be blank")
        if self.goal_revision < 1:
            raise ValueError("goal_revision must be >= 1")
        if not self.trigger_ref.strip():
            raise ValueError("trigger_ref must not be blank")
        for task_id in self.generated_task_ids:
            validate_id(task_id, "task")


@dataclass(frozen=True, slots=True, kw_only=True)
class GoalState:
    goal: Goal
    revision: int
    digest: str
    status: GoalStatus
    progress: GoalProgress
    success_criteria: tuple[SuccessCriterion, ...]
    constraints: GoalConstraints = field(default_factory=GoalConstraints)
    observation_policy: ObservationPolicy = field(default_factory=ObservationPolicy)
    task_generation_policy: TaskGenerationPolicy = field(default_factory=TaskGenerationPolicy)
    autonomy_policy: AutonomyPolicy = field(default_factory=AutonomyPolicy)
    deadline: datetime | None = None
    linked_tasks: tuple[GoalTaskLink, ...] = ()
    evidence: tuple[GoalEvidence, ...] = ()
    reviews: tuple[GoalReview, ...] = ()
    consecutive_failed_cycles: int = 0
    next_review_at: datetime | None = None
    terminal_reason: str | None = None
    created_actor_ref: str | None = None
    updated_actor_ref: str | None = None
    stream_revision: int = 0

    def __post_init__(self) -> None:
        validate_id(self.goal.id, "goal")
        if self.revision < 1:
            raise ValueError("goal revision must be >= 1")
        if not self.digest.strip():
            raise ValueError("goal digest must not be blank")
        if not self.success_criteria:
            raise ValueError("goal requires at least one explicit success criterion")
        if self.consecutive_failed_cycles < 0:
            raise ValueError("consecutive_failed_cycles must be >= 0")
        if self.stream_revision < 0:
            raise ValueError("stream_revision must be >= 0")

    @property
    def goal_id(self) -> str:
        return self.goal.id

    @property
    def title(self) -> str:
        return self.goal.title

    @property
    def objective(self) -> str:
        return self.goal.description

    @property
    def owner_ref(self) -> OwnerRef:
        return self.goal.owner_ref

    @property
    def project_id(self) -> str | None:
        return self.goal.project_id

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(
            link.task_id
            for link in self.linked_tasks
            if link.task_state is GoalTaskState.ACTIVE and link.valid_for_current_revision
        )

    def with_stream_revision(self, revision: int) -> GoalState:
        return replace(self, stream_revision=revision)


TERMINAL_GOAL_STATUSES = frozenset(
    {
        GoalStatus.SATISFIED,
        GoalStatus.FAILED,
        GoalStatus.CANCELLED,
        GoalStatus.SUPERSEDED,
    }
)

GOAL_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.DRAFT: frozenset({GoalStatus.ACTIVE, GoalStatus.CANCELLED}),
    GoalStatus.ACTIVE: frozenset(
        {
            GoalStatus.WAITING,
            GoalStatus.PAUSED,
            GoalStatus.SATISFIED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
            GoalStatus.SUPERSEDED,
        }
    ),
    GoalStatus.WAITING: frozenset(
        {
            GoalStatus.ACTIVE,
            GoalStatus.PAUSED,
            GoalStatus.SATISFIED,
            GoalStatus.FAILED,
            GoalStatus.CANCELLED,
            GoalStatus.SUPERSEDED,
        }
    ),
    GoalStatus.PAUSED: frozenset(
        {GoalStatus.ACTIVE, GoalStatus.CANCELLED, GoalStatus.SUPERSEDED}
    ),
    GoalStatus.SATISFIED: frozenset(),
    GoalStatus.FAILED: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
    GoalStatus.SUPERSEDED: frozenset(),
}


def require_goal_transition(current: GoalStatus, target: GoalStatus) -> None:
    if target not in GOAL_TRANSITIONS[current]:
        raise ValueError(f"illegal Goal transition: {current.value} -> {target.value}")


def goal_revision_digest(
    *,
    title: str,
    objective: str,
    success_criteria: tuple[SuccessCriterion, ...],
    constraints: GoalConstraints,
    observation_policy: ObservationPolicy,
    task_generation_policy: TaskGenerationPolicy,
    autonomy_policy: AutonomyPolicy,
    deadline: datetime | None,
) -> str:
    payload: dict[str, JsonValue] = {
        "title": title,
        "objective": objective,
        "success_criteria": [criterion_to_json(item) for item in success_criteria],
        "constraints": constraints_to_json(constraints),
        "observation_policy": observation_policy_to_json(observation_policy),
        "task_generation_policy": task_generation_policy_to_json(task_generation_policy),
        "autonomy_policy": autonomy_policy_to_json(autonomy_policy),
        "deadline": None if deadline is None else deadline.isoformat(),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deterministic_review_id(goal_id: str, goal_revision: int, review_key: str) -> str:
    return f"goal_review_{uuid5(NAMESPACE_URL, f'{goal_id}:{goal_revision}:{review_key}')}"


def deterministic_goal_task_id(goal_id: str, goal_revision: int, review_key: str, index: int) -> str:
    return f"task_{uuid5(NAMESPACE_URL, f'{goal_id}:{goal_revision}:{review_key}:{index}')}"


def criterion_to_json(value: SuccessCriterion) -> dict[str, JsonValue]:
    return {
        "criterion_id": value.criterion_id,
        "kind": value.kind.value,
        "description": value.description,
        "operator": value.operator,
        "target": value.target,
        "required": value.required,
    }


def constraints_to_json(value: GoalConstraints) -> dict[str, JsonValue]:
    return {
        "requirements": list(value.requirements),
        "out_of_scope": list(value.out_of_scope),
        "risk_requirements": list(value.risk_requirements),
        "data_requirements": list(value.data_requirements),
        "security_requirements": list(value.security_requirements),
    }


def observation_policy_to_json(value: ObservationPolicy) -> dict[str, JsonValue]:
    return {
        "automation_id": value.automation_id,
        "review_interval_seconds": value.review_interval_seconds,
        "event_types": list(value.event_types),
    }


def task_generation_policy_to_json(value: TaskGenerationPolicy) -> dict[str, JsonValue]:
    return {
        "enabled": value.enabled,
        "task_title": value.task_title,
        "proposal_required": value.proposal_required,
    }


def autonomy_policy_to_json(value: AutonomyPolicy) -> dict[str, JsonValue]:
    return {
        "max_tasks_per_review": value.max_tasks_per_review,
        "max_consecutive_failed_cycles": value.max_consecutive_failed_cycles,
        "human_checkpoint_required": value.human_checkpoint_required,
    }


def make_goal(
    *,
    title: str,
    objective: str,
    owner_ref: OwnerRef,
    project_id: str | None,
    actor_ref: str | None,
    goal_id: str | None = None,
) -> Goal:
    return Goal(
        id=goal_id or new_id("goal"),
        title=title,
        description=objective,
        owner_ref=owner_ref,
        project_id=project_id,
        provenance=Provenance(source="goal-service", actor_ref=actor_ref),
    )


__all__ = [
    "AutonomyPolicy",
    "CriterionEvaluation",
    "CriterionOperator",
    "GOAL_TRANSITIONS",
    "GoalConstraints",
    "GoalCriterionKind",
    "GoalCriterionState",
    "GoalEvidence",
    "GoalProgress",
    "GoalReview",
    "GoalState",
    "GoalStatus",
    "GoalTaskLink",
    "GoalTaskState",
    "ObservationPolicy",
    "SuccessCriterion",
    "TERMINAL_GOAL_STATUSES",
    "TaskGenerationPolicy",
    "TaskRevisionPolicy",
    "autonomy_policy_to_json",
    "constraints_to_json",
    "criterion_to_json",
    "deterministic_goal_task_id",
    "deterministic_review_id",
    "goal_revision_digest",
    "make_goal",
    "observation_policy_to_json",
    "require_goal_transition",
    "task_generation_policy_to_json",
]
