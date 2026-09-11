"""Focused read/query mechanics for the platform Task/Run kernel.

This module owns canonical kernel lookups and read-only Run selection helpers.  It does
not mutate lifecycle state or expose repositories outside the kernel package; the public
``PlatformKernel`` façade remains the supported caller surface.
"""

from __future__ import annotations

from typing import Literal

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import validate_id

from .models import TERMINAL_RUN_STATUSES, RunState, TaskState
from .repository import EventRepository, RunRepository, TaskRepository

RunSubjectType = Literal["task", "step"]


class KernelQueries:
    """Own read-only Task/Run/Event access and Run-selection mechanics."""

    def __init__(
        self,
        *,
        repository: EventRepository,
        task_repository: TaskRepository,
        run_repository: RunRepository,
    ) -> None:
        self._repository = repository
        self._tasks = task_repository
        self._runs = run_repository

    async def get_task(self, task_id: str) -> TaskState:
        validate_id(task_id, "task")
        return await self._tasks.get_task(task_id)

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        validate_id(task_id, "task")
        validate_id(run_id, "run")
        run = await self._runs.get_run(task_id, run_id)
        if run.run.correlation_id != task_id:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"run {run_id} does not belong to task {task_id}",
            )
        return run

    async def history(self, task_id: str) -> tuple[PlatformEvent, ...]:
        validate_id(task_id, "task")
        return await self._repository.read_events(task_id)

    @staticmethod
    def validate_run_subject(
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> None:
        if subject_type == "task":
            if subject_id != task.task_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"task run subject {subject_id} does not match owning task {task.task_id}",
                )
            return
        if task.plan_ref is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"task {task.task_id} has no canonical plan for step run {subject_id}",
            )
        if subject_id not in task.step_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"step {subject_id} does not belong to current plan {task.plan_ref} "
                f"for task {task.task_id}",
            )

    async def active_runs(self, task: TaskState) -> tuple[RunState, ...]:
        active: list[RunState] = []
        for run_id in task.run_ids:
            run = await self.get_run(task.task_id, run_id)
            if run.status not in TERMINAL_RUN_STATUSES:
                active.append(run)
        return tuple(active)

    async def active_run_for_subject(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> RunState | None:
        for run_id in reversed(task.run_ids):
            run = await self.get_run(task.task_id, run_id)
            if (
                run.run.subject_type == subject_type
                and run.run.subject_id == subject_id
                and run.status not in TERMINAL_RUN_STATUSES
            ):
                return run
        return None

    async def next_attempt(
        self,
        task: TaskState,
        subject_type: RunSubjectType,
        subject_id: str,
    ) -> int:
        latest = 0
        for run_id in task.run_ids:
            run = await self.get_run(task.task_id, run_id)
            if run.run.subject_type == subject_type and run.run.subject_id == subject_id:
                latest = max(latest, run.run.attempt)
        return latest + 1

    async def latest_active_run(self, task: TaskState) -> RunState | None:
        for run_id in reversed(task.run_ids):
            run = await self.get_run(task.task_id, run_id)
            if run.status not in TERMINAL_RUN_STATUSES:
                return run
        return None


__all__ = ["KernelQueries"]
