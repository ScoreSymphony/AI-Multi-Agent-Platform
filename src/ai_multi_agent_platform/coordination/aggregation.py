"""Canonical Task aggregation from terminal coordinated Steps."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.domain import StepStatus, TaskStatus
from ai_multi_agent_platform.kernel.models import TaskState

from .models import CoordinationPhase
from .repository import CoordinatorRepository

_TERMINAL_STEPS = frozenset(
    {StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED}
)


class AggregationKernel(Protocol):
    async def get_task(self, task_id: str) -> TaskState: ...

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...


class CoordinationAggregation:
    """Own the terminal Step-set to canonical Task outcome reduction."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: AggregationKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def aggregate_task(self, plan_id: str) -> None:
        state = self.repository.get_plan(plan_id)
        records = self.repository.list_step_records(plan_id)
        if len(records) != len(state.steps) or any(
            record.phase is not CoordinationPhase.TERMINAL for record in records
        ):
            return
        if not state.steps or any(step.status not in _TERMINAL_STEPS for step in state.steps):
            return
        task = await self.kernel.get_task(state.plan.task_id)
        if task.plan_ref != plan_id:
            return
        if task.status in {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}:
            return
        if any(step.status is StepStatus.FAILED for step in state.steps):
            if task.status in {TaskStatus.RUNNING, TaskStatus.WAITING}:
                await self.kernel.fail_task(
                    idempotency_key=f"coord:{plan_id}:aggregate:failed",
                    task_id=state.plan.task_id,
                    reason="canonical Plan contains a failed Step",
                    source="platform-coordinator",
                )
            return
        if any(step.status is StepStatus.CANCELLED for step in state.steps):
            if task.status in {
                TaskStatus.DRAFT,
                TaskStatus.READY,
                TaskStatus.RUNNING,
                TaskStatus.WAITING,
            }:
                await self.kernel.cancel_task(
                    idempotency_key=f"coord:{plan_id}:aggregate:cancelled",
                    task_id=state.plan.task_id,
                    source="platform-coordinator",
                )
            return
        if task.status is TaskStatus.RUNNING:
            await self.kernel.complete_task(
                idempotency_key=f"coord:{plan_id}:aggregate:succeeded",
                task_id=state.plan.task_id,
                source="platform-coordinator",
            )
