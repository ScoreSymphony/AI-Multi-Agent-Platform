from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum, StrEnum
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID, uuid4

SCHEMA_VERSION = "1.0"

CANONICAL_SUBJECT_PREFIXES: Mapping[str, str] = MappingProxyType(
    {
        "goal": "goal",
        "project": "project",
        "task": "task",
        "plan": "plan",
        "step": "step",
        "run": "run",
        "agent": "agent",
        "agent_team": "team",
        "artifact": "artifact",
        "result": "result",
        "event": "event",
        "approval": "approval",
        "node": "node",
        "worker": "worker",
        "worker_job": "worker_job",
        "tool": "tool",
        "tool_invocation": "tool_invocation",
        "capability": "cap",
        "policy_scope": "policy_scope",
        "model_assignment": "model_assignment",
    }
)

IMMUTABLE_LEAF_TYPES = (str, bytes, int, float, bool, type(None), datetime, UUID, Enum)


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


def validate_id(value: str, prefix: str) -> str:
    expected = f"{prefix}_"
    if not value.startswith(expected):
        raise ValueError(f"expected canonical {prefix} id")
    payload = value[len(expected) :]
    try:
        parsed = UUID(payload)
    except ValueError as exc:
        raise ValueError(f"invalid canonical {prefix} id") from exc
    if str(parsed) != payload.lower():
        raise ValueError(f"invalid canonical {prefix} id")
    return value


def validate_subject_id(subject_type: str, subject_id: str) -> str:
    prefix = CANONICAL_SUBJECT_PREFIXES.get(subject_type)
    if prefix is None:
        raise ValueError(f"unsupported canonical subject type: {subject_type}")
    return validate_id(subject_id, prefix)


def _validate_optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


def _validate_ids(values: tuple[str, ...], prefix: str) -> None:
    for value in values:
        validate_id(value, prefix)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, Enum):
        return _deep_freeze(value.value)
    if isinstance(value, IMMUTABLE_LEAF_TYPES):
        return value
    raise TypeError(f"unsupported mutable or noncanonical metadata value: {type(value).__name__}")


def _freeze_mapping_field(instance: object, name: str) -> None:
    object.__setattr__(instance, name, _deep_freeze(dict(getattr(instance, name))))


def _freeze_tuple_field(instance: object, name: str) -> None:
    object.__setattr__(instance, name, tuple(getattr(instance, name)))


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WorkerJobStatus(StrEnum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, kw_only=True)
class OwnerRef:
    type: Literal["user", "organization", "team", "service"]
    id: str


@dataclass(frozen=True, kw_only=True)
class ExternalRef:
    system: str
    kind: str
    value: str


@dataclass(frozen=True, kw_only=True)
class Provenance:
    source: str
    actor_ref: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _freeze_mapping_field(self, "details")


@dataclass(frozen=True, kw_only=True)
class Goal:
    title: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("goal"))
    project_id: str | None = None
    description: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "goal")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")


@dataclass(frozen=True, kw_only=True)
class Project:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("project"))
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "project")
        _freeze_tuple_field(self, "external_refs")


@dataclass(frozen=True, kw_only=True)
class Task:
    title: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("task"))
    status: TaskStatus = TaskStatus.DRAFT
    goal_id: str | None = None
    project_id: str | None = None
    description: str = ""
    correlation_id: str | None = None
    causation_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "task")
        _validate_optional_id(self.goal_id, "goal")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "metadata")

    def transition_to(self, target: TaskStatus) -> Task:
        from .lifecycle import TASK_TRANSITIONS, require_transition

        require_transition(self.status, target, TASK_TRANSITIONS)
        return replace(self, status=target, updated_at=utc_now())


@dataclass(frozen=True, kw_only=True)
class Plan:
    task_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("plan"))
    revision: int = 1
    active: bool = False
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "plan")
        validate_id(self.task_id, "task")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        if self.revision < 1:
            raise ValueError("plan revision must be >= 1")


@dataclass(frozen=True, kw_only=True)
class Step:
    plan_id: str
    title: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("step"))
    status: StepStatus = StepStatus.PENDING
    parent_step_id: str | None = None
    depends_on: tuple[str, ...] = ()
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "depends_on")
        _freeze_tuple_field(self, "external_refs")
        validate_id(self.id, "step")
        validate_id(self.plan_id, "plan")
        _validate_optional_id(self.parent_step_id, "step")
        _validate_ids(self.depends_on, "step")
        _validate_optional_id(self.project_id, "project")

    def transition_to(self, target: StepStatus) -> Step:
        from .lifecycle import STEP_TRANSITIONS, require_transition

        require_transition(self.status, target, STEP_TRANSITIONS)
        return replace(self, status=target, updated_at=utc_now())


@dataclass(frozen=True, kw_only=True)
class Run:
    subject_type: Literal["task", "step"]
    subject_id: str
    owner_ref: OwnerRef
    correlation_id: str
    id: str = field(default_factory=lambda: new_id("run"))
    attempt: int = 1
    status: RunStatus = RunStatus.QUEUED
    project_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None
    worker_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "run")
        validate_subject_id(self.subject_type, self.subject_id)
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.worker_id, "worker")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "metadata")
        if self.attempt < 1:
            raise ValueError("run attempt must be >= 1")

    def transition_to(self, target: RunStatus) -> Run:
        from .lifecycle import RUN_TRANSITIONS, require_transition

        require_transition(self.status, target, RUN_TRANSITIONS)
        now = utc_now()
        changes: dict[str, Any] = {"status": target, "updated_at": now}
        if target is RunStatus.RUNNING and self.started_at is None:
            changes["started_at"] = now
        if target in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }:
            changes["finished_at"] = now
        return replace(self, **changes)


@dataclass(frozen=True, kw_only=True)
class Agent:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("agent"))
    role: str = ""
    capability_ids: tuple[str, ...] = ()
    project_id: str | None = None
    model_assignment_id: str | None = None
    policy_requirements: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "capability_ids")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "policy_requirements")
        validate_id(self.id, "agent")
        _validate_ids(self.capability_ids, "cap")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.model_assignment_id, "model_assignment")


@dataclass(frozen=True, kw_only=True)
class AgentTeam:
    name: str
    owner_ref: OwnerRef
    agent_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: new_id("team"))
    coordination_metadata: Mapping[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "agent_ids")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "coordination_metadata")
        validate_id(self.id, "team")
        _validate_ids(self.agent_ids, "agent")
        _validate_optional_id(self.project_id, "project")


@dataclass(frozen=True, kw_only=True)
class Artifact:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("artifact"))
    media_type: str = "application/octet-stream"
    uri: str | None = None
    version: str | None = None
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "artifact")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")


@dataclass(frozen=True, kw_only=True)
class Result:
    subject_type: Literal["task", "run"]
    subject_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("result"))
    outcome: str = ""
    status_data: Mapping[str, Any] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "artifact_ids")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "status_data")
        validate_id(self.id, "result")
        validate_subject_id(self.subject_type, self.subject_id)
        _validate_ids(self.artifact_ids, "artifact")
        _validate_optional_id(self.project_id, "project")


@dataclass(frozen=True, kw_only=True)
class Event:
    event_type: str
    subject_type: str
    subject_id: str
    correlation_id: str
    id: str = field(default_factory=lambda: new_id("event"))
    owner_ref: OwnerRef | None = None
    project_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "event")
        validate_subject_id(self.subject_type, self.subject_id)
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "payload")


@dataclass(frozen=True, kw_only=True)
class Approval:
    subject_type: str
    subject_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("approval"))
    status: ApprovalStatus = ApprovalStatus.PENDING
    reason: str = ""
    decision_by: OwnerRef | None = None
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "approval")
        validate_subject_id(self.subject_type, self.subject_id)
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")

    def transition_to(self, target: ApprovalStatus) -> Approval:
        from .lifecycle import APPROVAL_TRANSITIONS, require_transition

        require_transition(self.status, target, APPROVAL_TRANSITIONS)
        return replace(self, status=target, updated_at=utc_now())


@dataclass(frozen=True, kw_only=True)
class Capability:
    name: str
    id: str = field(default_factory=lambda: new_id("cap"))
    description: str = ""
    attributes: Mapping[str, Any] = field(default_factory=dict)
    owner_ref: OwnerRef | None = None
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "cap")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "attributes")


@dataclass(frozen=True, kw_only=True)
class PolicyScope:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("policy_scope"))
    criteria: Mapping[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "policy_scope")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "criteria")


@dataclass(frozen=True, kw_only=True)
class Tool:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("tool"))
    capability_ids: tuple[str, ...] = ()
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "capability_ids")
        _freeze_tuple_field(self, "external_refs")
        validate_id(self.id, "tool")
        _validate_ids(self.capability_ids, "cap")
        _validate_optional_id(self.project_id, "project")


@dataclass(frozen=True, kw_only=True)
class ToolInvocation:
    tool_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("tool_invocation"))
    project_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "tool_invocation")
        validate_id(self.tool_id, "tool")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")


@dataclass(frozen=True, kw_only=True)
class ModelAssignment:
    subject_type: Literal["agent", "task", "step", "capability", "policy"]
    subject_id: str
    owner_ref: OwnerRef
    requirements: Mapping[str, Any]
    id: str = field(default_factory=lambda: new_id("model_assignment"))
    provider_ref: str | None = None
    revision: int = 1
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "model_assignment")
        if self.subject_type == "policy":
            validate_id(self.subject_id, "policy_scope")
        else:
            validate_subject_id(self.subject_type, self.subject_id)
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "requirements")
        if self.revision < 1:
            raise ValueError("model assignment revision must be >= 1")


@dataclass(frozen=True, kw_only=True)
class Node:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("node"))
    capability_ids: tuple[str, ...] = ()
    project_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "capability_ids")
        _freeze_tuple_field(self, "external_refs")
        _freeze_mapping_field(self, "metadata")
        validate_id(self.id, "node")
        _validate_ids(self.capability_ids, "cap")
        _validate_optional_id(self.project_id, "project")


@dataclass(frozen=True, kw_only=True)
class Worker:
    node_id: str
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("worker"))
    capability_ids: tuple[str, ...] = ()
    available: bool = True
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        _freeze_tuple_field(self, "capability_ids")
        _freeze_tuple_field(self, "external_refs")
        validate_id(self.id, "worker")
        validate_id(self.node_id, "node")
        _validate_ids(self.capability_ids, "cap")
        _validate_optional_id(self.project_id, "project")


@dataclass(frozen=True, kw_only=True)
class WorkerJob:
    run_id: str
    worker_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("worker_job"))
    status: WorkerJobStatus = WorkerJobStatus.QUEUED
    project_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    trace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "worker_job")
        validate_id(self.run_id, "run")
        validate_id(self.worker_id, "worker")
        _validate_optional_id(self.project_id, "project")
        _freeze_tuple_field(self, "external_refs")

    def transition_to(self, target: WorkerJobStatus) -> WorkerJob:
        from .lifecycle import WORKER_JOB_TRANSITIONS, require_transition

        require_transition(self.status, target, WORKER_JOB_TRANSITIONS)
        now = utc_now()
        changes: dict[str, Any] = {"status": target, "updated_at": now}
        if target is WorkerJobStatus.RUNNING and self.started_at is None:
            changes["started_at"] = now
        if target in {
            WorkerJobStatus.SUCCEEDED,
            WorkerJobStatus.FAILED,
            WorkerJobStatus.CANCELLED,
            WorkerJobStatus.TIMED_OUT,
        }:
            changes["finished_at"] = now
        return replace(self, **changes)
