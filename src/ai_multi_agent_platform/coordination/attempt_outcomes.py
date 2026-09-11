"""Canonical Step-attempt outcome observation and retry scheduling."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import RunStatus, Step, StepStatus
from ai_multi_agent_platform.kernel.models import RunState
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


class AttemptOutcomeKernel(Protocol):
    """Narrow canonical Run read capability required by outcome observation."""

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...


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


class OutcomeEmitter(Protocol):
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


@dataclass(frozen=True, slots=True)
class AttemptOutcomeMutation:
    plan_id: str
    changed: bool


class CoordinationAttemptOutcomes:
    """Own terminal Run observation, retry decisions and due retry activation."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: AttemptOutcomeKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def observe_run(
        self,
        *,
        task_id: str,
        run_id: str,
        failure_category: str | None,
        observation_key: str | None,
        now: datetime,
        claim: ClaimProvider,
        close_wait: CloseWait,
        emit: OutcomeEmitter,
    ) -> AttemptOutcomeMutation:
        run = await self.kernel.get_run(task_id, run_id)
        if run.run.subject_type != "step":
            raise ContractError(ErrorCode.INVALID_REQUEST, "coordinator accepts only Step Runs")
        if run.status not in _TERMINAL_RUNS:
            raise ContractError(ErrorCode.CONFLICT, f"run {run_id} is not terminal")
        record = self.repository.get_step_record(run.run.subject_id)
        if record.task_id != task_id:
            raise ContractError(ErrorCode.CONFLICT, "Run/Step task scope mismatch")
        key = observation_key or f"run:{run_id}:{run.status.value}"
        if key in record.processed_keys:
            return AttemptOutcomeMutation(plan_id=record.plan_id, changed=False)

        step_claim = claim(record.step_id, now)
        if step_claim is None:
            return AttemptOutcomeMutation(plan_id=record.plan_id, changed=False)
        try:
            state = self.repository.get_plan(record.plan_id)
            step = state.step(record.step_id)
            current = self.repository.get_step_record(record.step_id)
            if key in current.processed_keys:
                return AttemptOutcomeMutation(plan_id=current.plan_id, changed=False)
            next_step = step
            next_record = replace(
                current,
                processed_keys=(*current.processed_keys, key),
                latest_run_id=run_id,
            )

            if run.status is RunStatus.SUCCEEDED:
                if step.status is StepStatus.RUNNING:
                    next_step = step.transition_to(StepStatus.SUCCEEDED)
                    retry_state = (
                        RetryState.COMPLETED
                        if current.retry_state is RetryState.ACTIVE
                        else current.retry_state
                    )
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.TERMINAL,
                        retry_state=retry_state,
                    )
                elif step.status is StepStatus.WAITING and current.wait is not None:
                    next_record = replace(next_record, phase=CoordinationPhase.WAITING)
                elif step.status is not StepStatus.SUCCEEDED:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.INCONSISTENT,
                        reconciliation=ReconciliationDisposition.INCONSISTENT,
                        reconciliation_detail="successful Run conflicts with canonical Step status",
                    )
            elif run.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                category = failure_category or run.status.value
                if step.status in {StepStatus.RUNNING, StepStatus.WAITING}:
                    next_step = step.transition_to(StepStatus.FAILED)
                next_attempt = max(current.current_attempt, run.attempt) + 1
                category_retryable = category in current.retry_policy.retryable_categories
                closed_wait = close_wait(
                    current.wait,
                    resolution=WaitResolution.CANCELLED,
                    resolution_key=key,
                    now=now,
                )
                if category_retryable and next_attempt <= current.retry_policy.max_attempts:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.RETRY_SCHEDULED,
                        current_attempt=max(current.current_attempt, run.attempt),
                        retry_due_at=now + current.retry_policy.delay_for_attempt(next_attempt),
                        retry_state=RetryState.SCHEDULED,
                        wait=closed_wait,
                    )
                    emit(
                        "coordination.retry.scheduled",
                        task_id,
                        current.plan_id,
                        current.step_id,
                        run_id=run_id,
                        attributes={"attempt": next_attempt, "category": category},
                    )
                elif category_retryable:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.TERMINAL,
                        current_attempt=max(current.current_attempt, run.attempt),
                        retry_due_at=None,
                        retry_state=RetryState.EXHAUSTED,
                        wait=closed_wait,
                    )
                    emit(
                        "coordination.retry.exhausted",
                        task_id,
                        current.plan_id,
                        current.step_id,
                        run_id=run_id,
                        outcome=TelemetryOutcome.FAILED,
                        attributes={"category": category},
                    )
                else:
                    next_record = replace(
                        next_record,
                        phase=CoordinationPhase.TERMINAL,
                        current_attempt=max(current.current_attempt, run.attempt),
                        retry_due_at=None,
                        retry_state=RetryState.NOT_RETRYABLE,
                        wait=closed_wait,
                    )
                    emit(
                        "coordination.retry.not_retryable",
                        task_id,
                        current.plan_id,
                        current.step_id,
                        run_id=run_id,
                        outcome=TelemetryOutcome.FAILED,
                        attributes={"category": category},
                    )
            else:
                if step.status in {StepStatus.RUNNING, StepStatus.WAITING}:
                    next_step = step.transition_to(StepStatus.CANCELLED)
                retry_state = (
                    RetryState.CANCELLED
                    if current.retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}
                    else current.retry_state
                )
                next_record = replace(
                    next_record,
                    phase=CoordinationPhase.TERMINAL,
                    retry_due_at=None,
                    retry_state=retry_state,
                    wait=close_wait(
                        current.wait,
                        resolution=WaitResolution.CANCELLED,
                        resolution_key=key,
                        now=now,
                    ),
                )

            self.repository.save_step(
                step=next_step,
                record=next_record,
                expected_revision=current.revision,
                claim=step_claim,
                now=now,
            )
            emit(
                "coordination.run.observed",
                task_id,
                current.plan_id,
                current.step_id,
                run_id=run_id,
                outcome=self.run_outcome(run.status),
            )
            return AttemptOutcomeMutation(plan_id=current.plan_id, changed=True)
        finally:
            self.repository.release_claim(step_claim)

    async def activate_retry(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
        *,
        claim: ClaimProvider,
        emit: OutcomeEmitter,
    ) -> bool:
        if step.status is not StepStatus.FAILED:
            return False
        step_claim = claim(step.id, now)
        if step_claim is None:
            return False
        try:
            current = self.repository.get_step_record(step.id)
            current_step = self.repository.get_plan(step.plan_id).step(step.id)
            if (
                current.phase is not CoordinationPhase.RETRY_SCHEDULED
                or current.retry_due_at is None
                or current.retry_due_at > now
            ):
                return False
            ready = current_step.transition_to(StepStatus.READY)
            updated = replace(
                current,
                phase=CoordinationPhase.READY,
                retry_due_at=None,
                retry_state=RetryState.ACTIVE,
            )
            self.repository.save_step(
                step=ready,
                record=updated,
                expected_revision=current.revision,
                claim=step_claim,
                now=now,
            )
            emit(
                "coordination.retry.started",
                current.task_id,
                current.plan_id,
                current.step_id,
                attributes={"attempt": current.current_attempt + 1},
            )
            return True
        finally:
            self.repository.release_claim(step_claim)

    @staticmethod
    def run_outcome(status: RunStatus) -> TelemetryOutcome:
        return {
            RunStatus.SUCCEEDED: TelemetryOutcome.SUCCEEDED,
            RunStatus.FAILED: TelemetryOutcome.FAILED,
            RunStatus.CANCELLED: TelemetryOutcome.CANCELLED,
            RunStatus.TIMED_OUT: TelemetryOutcome.TIMED_OUT,
        }.get(status, TelemetryOutcome.UNKNOWN)
