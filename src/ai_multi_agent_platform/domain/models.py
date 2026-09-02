from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid4

SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


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
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "task")
        if self.goal_id is not None:
            validate_id(self.goal_id, "goal")
        if self.project_id is not None:
            validate_id(self.project_id, "project")


@dataclass(kw_only=True)
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
        if self.revision < 1:
            raise ValueError("plan revision must be >= 1")


@dataclass(kw_only=True)
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
        validate_id(self.id, "step")
        validate_id(self.plan_id, "plan")
        if self.parent_step_id is not None:
            validate_id(self.parent_step_id, "step")
        for dependency in self.depends_on:
            validate_id(dependency, "step")


@dataclass(kw_only=True)
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.id, "run")
        validate_id(self.subject_id, self.subject_type)
        if self.attempt < 1:
            raise ValueError("run attempt must be >= 1")
        if self.worker_id is not None:
            validate_id(self.worker_id, "worker")


@dataclass(kw_only=True)
class Agent:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("agent"))
    role: str = ""
    capability_ids: tuple[str, ...] = ()
    project_id: str | None = None
    model_assignment_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "agent")


@dataclass(kw_only=True)
class AgentTeam:
    name: str
    owner_ref: OwnerRef
    agent_ids: tuple[str, ...]
    id: str = field(default_factory=lambda: new_id("team"))
    coordination_metadata: dict[str, Any] = field(default_factory=dict)
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "team")
        for agent_id in self.agent_ids:
            validate_id(agent_id, "agent")


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
class Result:
    subject_type: Literal["task", "run"]
    subject_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("result"))
    outcome: str = ""
    artifact_ids: tuple[str, ...] = ()
    project_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "result")
        validate_id(self.subject_id, self.subject_type)
        for artifact_id in self.artifact_ids:
            validate_id(artifact_id, "artifact")


@dataclass(frozen=True, kw_only=True)
class Event:
    event_type: str
    subject_type: str
    subject_id: str
    correlation_id: str
    id: str = field(default_factory=lambda: new_id("event"))
    causation_id: str | None = None
    trace_id: str | None = None
    occurred_at: datetime = field(default_factory=utc_now)
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "event")


@dataclass(kw_only=True)
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


@dataclass(kw_only=True)
class Capability:
    name: str
    id: str = field(default_factory=lambda: new_id("cap"))
    description: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.id, "cap")


@dataclass(kw_only=True)
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
        validate_id(self.id, "tool")


@dataclass(kw_only=True)
class ModelAssignment:
    subject_type: Literal["agent", "task", "step", "policy"]
    subject_id: str
    owner_ref: OwnerRef
    requirements: dict[str, Any]
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
        if self.revision < 1:
            raise ValueError("model assignment revision must be >= 1")


@dataclass(kw_only=True)
class Node:
    name: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("node"))
    capability_ids: tuple[str, ...] = ()
    project_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "node")


@dataclass(kw_only=True)
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
        validate_id(self.id, "worker")
        validate_id(self.node_id, "node")


@dataclass(kw_only=True)
class WorkerJob:
    run_id: str
    worker_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("worker_job"))
    status: WorkerJobStatus = WorkerJobStatus.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    schema_version: str = SCHEMA_VERSION
    provenance: Provenance | None = None
    external_refs: tuple[ExternalRef, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.id, "worker_job")
        validate_id(self.run_id, "run")
        validate_id(self.worker_id, "worker")
