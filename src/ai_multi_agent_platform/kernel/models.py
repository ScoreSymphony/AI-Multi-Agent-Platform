"""Canonical event-sourced read models for the platform-owned kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Run, RunStatus, Task, TaskStatus

TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }
)


class RecoveryDisposition(StrEnum):
    """How recovery classified or reconciled one canonical run."""

    QUEUED_PENDING = "queued_pending"
    REDISPATCHED = "redispatched"
    RECONCILED = "reconciled"
    TERMINAL_UNCHANGED = "terminal_unchanged"
    ORPHANED_RECONCILIATION_REQUIRED = "orphaned_reconciliation_required"


@dataclass(frozen=True, slots=True)
class TaskState:
    """Externally visible task state reconstructed only from canonical events."""

    task: Task
    revision: int
    plan_ref: str | None = None
    step_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    wait_reason: str | None = None
    blocked: bool = False

    @property
    def task_id(self) -> str:
        return self.task.id

    @property
    def status(self) -> TaskStatus:
        return self.task.status


@dataclass(frozen=True, slots=True)
class RunState:
    """Externally visible run state reconstructed only from canonical events."""

    run: Run
    revision: int
    backend_ref: str | None = None
    output: dict[str, JsonValue] = field(default_factory=dict)
    artifact_ids: tuple[str, ...] = ()
    result_ids: tuple[str, ...] = ()
    dispatch_attempts: int = 0
    recovery_required: bool = False
    recovery_reason: str | None = None

    @property
    def run_id(self) -> str:
        return self.run.id

    @property
    def task_id(self) -> str:
        return self.run.correlation_id

    @property
    def attempt(self) -> int:
        return self.run.attempt

    @property
    def status(self) -> RunStatus:
        return self.run.status


@dataclass(frozen=True, slots=True)
class RecoveryEntry:
    run_id: str
    before: RunStatus
    after: RunStatus
    disposition: RecoveryDisposition


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    task_id: str
    entries: tuple[RecoveryEntry, ...]
