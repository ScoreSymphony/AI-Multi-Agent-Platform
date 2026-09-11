"""Canonical Plan/Step cancellation for durable coordination."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import RunStatus, StepStatus, TaskStatus
from ai_multi_agent_platform.kernel.models import RunState, TaskState
from ai_multi_agent_platform.observability import TelemetryOutcome

from .models import (
    CoordinationPhase,
    CoordinatorClaim,
    ReconciliationDisposition,
    RetryState,
    StepCoordinationRecord,
    StepWait,
    WaitResolution,
)
from .repository import CoordinatorRepository

_TERMINAL_RUNS = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)


class CancellationKernel(Protocol):
    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...

    async def get_task(self, task_id: str) -> TaskState: ...

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState: ...


class ClaimProvider(Protocol):
    def __call__(self, step_id: str, now: datetime) -> CoordinatorClaim | None: ...


class CloseWait(Protocol):
    def __call__(
        self,
        wait: StepWait | None,
        *,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> StepWait | None: ...


class CancellationEmitter(Protocol):
    def __call__(
        self,
        event_name: str,
        task_id: str,
        plan_id: str,
        step_id: str | None,
        *,
        run_id: str | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None: ...


class CoordinationCancellation:
    """Own Plan cancellation and canonical active-Run cancellation propagation."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: CancellationKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def cancel_plan(
        self,
        plan_id: str,
        *,
        idempotency_key: str,
        now: datetime,
        claim: ClaimProvider,
        close_wait: CloseWait,
        emit: CancellationEmitter,
    ) -> str:
        if not idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")
        state = self.repository.get_plan(plan_id)
        for record in self.repository.list_step_records(plan_id):
            step = self.repository.get_plan(plan_id).step(record.step_id)
            if step.status in {StepStatus.SUCCEEDED, StepStatus.SKIPPED, StepStatus.CANCELLED}:
                continue
            step_claim = claim(step.id, now)
            if step_claim is None:
                continue
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(plan_id).step(step.id)
                await self.cancel_active_run(current, f"{idempotency_key}:run:{step.id}")
                if current_step.status in {
                    StepStatus.PENDING,
                    StepStatus.READY,
                    StepStatus.RUNNING,
                    StepStatus.WAITING,
                }:
                    current_step = current_step.transition_to(StepStatus.CANCELLED)
                retry_state = (
                    RetryState.CANCELLED
                    if current.retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}
                    else current.retry_state
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    retry_due_at=None,
                    retry_state=retry_state,
                    wait=close_wait(
                        current.wait,
                        resolution=WaitResolution.CANCELLED,
                        resolution_key=f"{idempotency_key}:wait:{step.id}",
                        now=now,
                    ),
                    reconciliation=ReconciliationDisposition.CANONICAL_TERMINAL,
                )
                self.repository.save_step(
                    step=current_step,
                    record=updated,
                    expected_revision=current.revision,
                    claim=step_claim,
                    now=now,
                )
                emit(
                    "coordination.step.cancelled",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    run_id=current.latest_run_id,
                    outcome=TelemetryOutcome.CANCELLED,
                    attributes={"source": "plan_cancellation"},
                )
            finally:
                self.repository.release_claim(step_claim)
        task = await self.kernel.get_task(state.plan.task_id)
        if task.status is not TaskStatus.CANCELLED:
            await self.kernel.cancel_task(
                idempotency_key=f"{idempotency_key}:task",
                task_id=state.plan.task_id,
                source="platform-coordinator",
            )
        emit("coordination.plan.cancelled", state.plan.task_id, plan_id, None)
        return plan_id

    async def cancel_active_run(self, record: StepCoordinationRecord, key: str) -> None:
        if record.latest_run_id is None:
            return
        run = await self.kernel.get_run(record.task_id, record.latest_run_id)
        if run.status not in _TERMINAL_RUNS:
            await self.kernel.cancel_run(
                idempotency_key=key,
                task_id=record.task_id,
                run_id=record.latest_run_id,
                source="platform-coordinator",
            )
