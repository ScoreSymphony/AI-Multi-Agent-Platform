"""Canonical compensation records for explicitly reversible external side effects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.capabilities import (
    CompensationDescriptor,
    ReversibilityClassification,
)
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.domain import new_id, validate_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def _freeze(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


class CompensationTrigger(StrEnum):
    MANUAL = "manual"
    DOWNSTREAM_FAILURE = "downstream_failure"
    CANCELLATION = "cancellation"
    RECONCILIATION = "reconciliation"


class CompensationAutomation(StrEnum):
    NEVER = "never"
    DOWNSTREAM_FAILURE = "downstream_failure"
    FAILURE_OR_CANCELLATION = "failure_or_cancellation"


class CompensationFailureMode(StrEnum):
    STOP_AND_ESCALATE = "stop_and_escalate"
    CONTINUE_AND_ESCALATE = "continue_and_escalate"


class CompensationStatus(StrEnum):
    REQUESTED = "requested"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    APPROVAL_REQUIRED = "approval_required"
    DENIED = "denied"
    EXPIRED = "expired"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    NOT_COMPENSABLE = "not_compensable"


@dataclass(frozen=True, slots=True)
class CompensationPolicy:
    """Explicit boundary policy. The conservative default never compensates automatically."""

    automation: CompensationAutomation = CompensationAutomation.NEVER
    failure_mode: CompensationFailureMode = CompensationFailureMode.STOP_AND_ESCALATE
    require_human_approval: bool = False
    allow_newer_plan_revision: bool = False


@dataclass(frozen=True, slots=True)
class CompensationGroup:
    """Explicit compensation boundary for one canonical Plan revision."""

    group_id: str
    task_id: str
    plan_id: str
    plan_revision: int
    project_id: str | None = None
    policy: CompensationPolicy = field(default_factory=CompensationPolicy)
    created_at: datetime = field(default_factory=utc_now)
    provenance_source: str = "platform-compensation"

    def __post_init__(self) -> None:
        validate_id(self.group_id, "compensation_group")
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        if self.plan_revision < 1:
            raise ValueError("plan_revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if not self.provenance_source.strip():
            raise ValueError("provenance_source must not be blank")
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))


@dataclass(frozen=True, slots=True)
class CompletedSideEffect:
    """Immutable evidence snapshot of one completed side effect inside an explicit group."""

    action_id: str
    group_id: str
    task_id: str
    plan_id: str
    plan_revision: int
    project_id: str | None
    step_id: str
    run_id: str
    tool_invocation_id: str
    agent_id: str
    capability_id: str
    capability_version: str
    reversibility: ReversibilityClassification
    compensation: CompensationDescriptor | None
    original_arguments: Mapping[str, JsonValue]
    compensation_arguments: Mapping[str, JsonValue]
    execution_order: int
    depends_on_action_ids: tuple[str, ...] = ()
    original_result_ref: str | None = None
    external_resource_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    completed_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.action_id, "compensation_action")
        validate_id(self.group_id, "compensation_group")
        validate_id(self.task_id, "task")
        validate_id(self.plan_id, "plan")
        validate_id(self.step_id, "step")
        validate_id(self.run_id, "run")
        validate_id(self.tool_invocation_id, "tool_invocation")
        validate_id(self.agent_id, "agent")
        if self.plan_revision < 1:
            raise ValueError("plan_revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if not self.capability_id.strip() or not self.capability_version.strip():
            raise ValueError("capability identity must not be blank")
        if self.execution_order < 0:
            raise ValueError("execution_order must be >= 0")
        if len(set(self.depends_on_action_ids)) != len(self.depends_on_action_ids):
            raise ValueError("depends_on_action_ids must not contain duplicates")
        for action_id in self.depends_on_action_ids:
            validate_id(action_id, "compensation_action")
            if action_id == self.action_id:
                raise ValueError("side effect cannot depend on itself")
        if (
            self.reversibility
            in {
                ReversibilityClassification.IRREVERSIBLE,
                ReversibilityClassification.UNKNOWN,
            }
            and self.compensation is not None
        ):
            raise ValueError("irreversible/unknown action cannot carry compensation support")
        if (
            self.reversibility is ReversibilityClassification.REVERSIBLE
            and self.compensation is None
        ):
            raise ValueError("reversible action must carry a compensation descriptor")
        object.__setattr__(self, "original_arguments", _freeze(self.original_arguments))
        object.__setattr__(self, "compensation_arguments", _freeze(self.compensation_arguments))
        object.__setattr__(self, "completed_at", _aware(self.completed_at, "completed_at"))


@dataclass(frozen=True, slots=True)
class CompensationRequest:
    """Durable intent to compensate one immutable completed side effect."""

    compensation_id: str
    idempotency_key: str
    group_id: str
    action_id: str
    original_task_id: str
    original_plan_id: str
    original_plan_revision: int
    original_project_id: str | None
    original_step_id: str
    original_run_id: str
    original_tool_invocation_id: str
    original_result_ref: str | None
    external_resource_ref: str | None
    requested_capability_id: str | None
    requested_capability_version: str | None
    trigger: CompensationTrigger
    reason: str
    actor_ref: str
    correlation_id: str
    approval_id: str | None = None
    requested_at: datetime = field(default_factory=utc_now)
    provenance_source: str = "platform-compensation"

    def __post_init__(self) -> None:
        validate_id(self.compensation_id, "compensation")
        validate_id(self.group_id, "compensation_group")
        validate_id(self.action_id, "compensation_action")
        validate_id(self.original_task_id, "task")
        validate_id(self.original_plan_id, "plan")
        validate_id(self.original_step_id, "step")
        validate_id(self.original_run_id, "run")
        validate_id(self.original_tool_invocation_id, "tool_invocation")
        if self.original_plan_revision < 1:
            raise ValueError("original_plan_revision must be >= 1")
        if self.original_project_id is not None:
            validate_id(self.original_project_id, "project")
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        if not self.reason.strip() or not self.actor_ref.strip():
            raise ValueError("reason and actor_ref must not be blank")
        if not self.correlation_id.strip() or not self.provenance_source.strip():
            raise ValueError("correlation/provenance must not be blank")
        if self.requested_capability_id is None:
            if self.requested_capability_version is not None:
                raise ValueError("capability version requires a capability ID")
        elif not self.requested_capability_id.strip():
            raise ValueError("requested_capability_id must not be blank")
        if (
            self.requested_capability_version is not None
            and not self.requested_capability_version.strip()
        ):
            raise ValueError("requested_capability_version must not be blank")
        if self.approval_id is not None:
            validate_id(self.approval_id, "approval")
        object.__setattr__(self, "requested_at", _aware(self.requested_at, "requested_at"))


@dataclass(frozen=True, slots=True)
class CompensationExecutionContext:
    """An already-canonical Run/Agent context used for the ordinary compensating invocation."""

    invocation_id: str
    operation: OperationContext
    task_id: str
    run_id: str
    agent_id: str
    granted_permissions: frozenset[str] = frozenset()
    available_worker_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.invocation_id.strip():
            raise ValueError("invocation_id must not be blank")
        validate_id(self.task_id, "task")
        validate_id(self.run_id, "run")
        validate_id(self.agent_id, "agent")


@dataclass(frozen=True, slots=True)
class CompensationResult:
    """Durable result of compensation; original execution evidence remains untouched."""

    compensation_id: str
    status: CompensationStatus
    execution_task_id: str | None = None
    execution_run_id: str | None = None
    execution_agent_id: str | None = None
    invocation_id: str | None = None
    canonical_tool_invocation_id: str | None = None
    provider_id: str | None = None
    approval_id: str | None = None
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    manual_intervention_required: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    verification_ref: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.compensation_id, "compensation")
        for value, prefix in (
            (self.execution_task_id, "task"),
            (self.execution_run_id, "run"),
            (self.execution_agent_id, "agent"),
            (self.canonical_tool_invocation_id, "tool_invocation"),
            (self.approval_id, "approval"),
        ):
            if value is not None:
                validate_id(value, prefix)
        if self.started_at is not None:
            object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))
        if self.completed_at is not None:
            object.__setattr__(self, "completed_at", _aware(self.completed_at, "completed_at"))


@dataclass(frozen=True, slots=True)
class CompensationReconciliation:
    """Evidence returned after a restart where the external effect may already have happened."""

    outcome_known: bool
    succeeded: bool = False
    result_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class CompensationActionProjection:
    action: CompletedSideEffect
    request: CompensationRequest | None
    result: CompensationResult | None


@dataclass(frozen=True, slots=True)
class CompensationGroupProjection:
    group: CompensationGroup
    actions: tuple[CompensationActionProjection, ...]

    @property
    def manual_intervention_required(self) -> bool:
        return any(
            item.result is not None and item.result.manual_intervention_required
            for item in self.actions
        )


def new_compensation_group_id() -> str:
    return new_id("compensation_group")


def new_compensation_action_id() -> str:
    return new_id("compensation_action")


def new_compensation_id() -> str:
    return new_id("compensation")
