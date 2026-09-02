"""Externally visible kernel state reconstructed from canonical events."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import ExecutionStatus, JsonValue


class TaskStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
TERMINAL_RUN_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.TIMED_OUT,
    }
)


@dataclass(frozen=True, slots=True)
class TaskView:
    task_id: str
    title: str
    objective: str
    status: TaskStatus
    owner_type: str | None = None
    owner_id: str | None = None
    project_id: str | None = None
    plan_ref: str | None = None
    run_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    result_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RunView:
    run_id: str
    task_id: str
    attempt: int
    status: ExecutionStatus
    backend_ref: str | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
