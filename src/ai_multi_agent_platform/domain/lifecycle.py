from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from .models import ApprovalStatus, RunStatus, StepStatus, TaskStatus, WorkerJobStatus

StatusT = TypeVar("StatusT", bound=StrEnum)

TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.READY, TaskStatus.CANCELLED}),
    TaskStatus.READY: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.WAITING, TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.WAITING: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.FAILED: frozenset({TaskStatus.READY}),
    TaskStatus.CANCELLED: frozenset(),
}

STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset({StepStatus.READY, StepStatus.SKIPPED, StepStatus.CANCELLED}),
    StepStatus.READY: frozenset({StepStatus.RUNNING, StepStatus.SKIPPED, StepStatus.CANCELLED}),
    StepStatus.RUNNING: frozenset(
        {StepStatus.WAITING, StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.CANCELLED}
    ),
    StepStatus.WAITING: frozenset({StepStatus.RUNNING, StepStatus.FAILED, StepStatus.CANCELLED}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset({StepStatus.READY}),
    StepStatus.SKIPPED: frozenset(),
    StepStatus.CANCELLED: frozenset(),
}

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.STARTING, RunStatus.CANCELLED}),
    RunStatus.STARTING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.TIMED_OUT: frozenset(),
}

APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.CANCELLED: frozenset(),
}

WORKER_JOB_TRANSITIONS: dict[WorkerJobStatus, frozenset[WorkerJobStatus]] = {
    WorkerJobStatus.QUEUED: frozenset({WorkerJobStatus.ASSIGNED, WorkerJobStatus.CANCELLED}),
    WorkerJobStatus.ASSIGNED: frozenset({WorkerJobStatus.STARTING, WorkerJobStatus.CANCELLED}),
    WorkerJobStatus.STARTING: frozenset(
        {WorkerJobStatus.RUNNING, WorkerJobStatus.FAILED, WorkerJobStatus.CANCELLED}
    ),
    WorkerJobStatus.RUNNING: frozenset(
        {
            WorkerJobStatus.WAITING,
            WorkerJobStatus.SUCCEEDED,
            WorkerJobStatus.FAILED,
            WorkerJobStatus.CANCELLED,
            WorkerJobStatus.TIMED_OUT,
        }
    ),
    WorkerJobStatus.WAITING: frozenset(
        {WorkerJobStatus.RUNNING, WorkerJobStatus.FAILED, WorkerJobStatus.CANCELLED}
    ),
    WorkerJobStatus.SUCCEEDED: frozenset(),
    WorkerJobStatus.FAILED: frozenset(),
    WorkerJobStatus.CANCELLED: frozenset(),
    WorkerJobStatus.TIMED_OUT: frozenset(),
}


def can_transition(
    current: StatusT, target: StatusT, transitions: dict[StatusT, frozenset[StatusT]]
) -> bool:
    return target in transitions[current]


def require_transition(
    current: StatusT, target: StatusT, transitions: dict[StatusT, frozenset[StatusT]]
) -> None:
    if not can_transition(current, target, transitions):
        raise ValueError(f"illegal lifecycle transition: {current.value} -> {target.value}")
