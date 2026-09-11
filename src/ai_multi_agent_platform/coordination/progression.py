"""Dependency-barrier progression and canonical Step-attempt dispatch."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Literal, Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Step, StepStatus
from ai_multi_agent_platform.kernel.models import RunState
from ai_multi_agent_platform.observability import TelemetryOutcome

from .models import (
    CoordinationPhase,
    CoordinatorClaim,
    PredecessorFailurePolicy,
    ReconciliationDisposition,
    StepCoordinationRecord,
)
from .repository import CoordinatorRepository

_SUCCESSFUL_PREDECESSORS = frozenset({StepStatus.SUCCEEDED, StepStatus.SKIPPED})


class ProgressionRunKernel(Protocol):
    """Narrow canonical Run capabilities required by Step progression."""

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: Literal["task", "step"] = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...


class ClaimProvider(Protocol):
    def __call__(self, step_id: str, now: datetime) -> CoordinatorClaim | None: ...


class CoordinationEmitter(Protocol):
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


class CoordinationProgression:
    """Own dependency barrier transitions and creation/dispatch of Step attempts."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: ProgressionRunKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def refresh_dependencies(
        self,
        step: Step,
        record: StepCoordinationRecord,
        by_id: dict[str, Step],
        now: datetime,
        *,
        claim: ClaimProvider,
        emit: CoordinationEmitter,
    ) -> bool:
        if step.status is not StepStatus.PENDING:
            return False
        satisfied = tuple(
            dependency_id
            for dependency_id in record.dependency_ids
            if by_id[dependency_id].status in _SUCCESSFUL_PREDECESSORS
        )
        failed = tuple(
            dependency_id
            for dependency_id in record.dependency_ids
            if by_id[dependency_id].status in {StepStatus.FAILED, StepStatus.CANCELLED}
        )
        if failed:
            step_claim = claim(step.id, now)
            if step_claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                if current_step.status is not StepStatus.PENDING:
                    return False
                terminal = current_step.transition_to(
                    StepStatus.CANCELLED
                    if current.predecessor_failure_policy
                    is PredecessorFailurePolicy.CANCEL_DEPENDENT
                    else StepStatus.SKIPPED
                )
                updated = replace(
                    current,
                    phase=CoordinationPhase.TERMINAL,
                    satisfied_dependency_ids=satisfied,
                    reconciliation_detail="predecessor failed or cancelled",
                )
                self.repository.save_step(
                    step=terminal,
                    record=updated,
                    expected_revision=current.revision,
                    claim=step_claim,
                    now=now,
                )
                emit(
                    "coordination.barrier.failed",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    outcome=TelemetryOutcome.FAILED,
                    attributes={"failed_predecessors": list(failed)},
                )
                return True
            finally:
                self.repository.release_claim(step_claim)

        if set(satisfied) != set(record.satisfied_dependency_ids):
            step_claim = claim(step.id, now)
            if step_claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                updated = replace(current, satisfied_dependency_ids=satisfied)
                if set(satisfied) == set(current.dependency_ids):
                    current_step = current_step.transition_to(StepStatus.READY)
                    updated = replace(updated, phase=CoordinationPhase.READY)
                self.repository.save_step(
                    step=current_step,
                    record=updated,
                    expected_revision=current.revision,
                    claim=step_claim,
                    now=now,
                )
                emit(
                    "coordination.barrier.progress",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    attributes={
                        "satisfied": len(satisfied),
                        "required": len(current.dependency_ids),
                    },
                )
                if updated.phase is CoordinationPhase.READY:
                    emit(
                        "coordination.barrier.completed",
                        current.task_id,
                        current.plan_id,
                        current.step_id,
                        outcome=TelemetryOutcome.SUCCEEDED,
                        attributes={"required": len(current.dependency_ids)},
                    )
                    emit(
                        "coordination.step.ready",
                        current.task_id,
                        current.plan_id,
                        current.step_id,
                    )
                return True
            finally:
                self.repository.release_claim(step_claim)

        if not record.dependency_ids:
            step_claim = claim(step.id, now)
            if step_claim is None:
                return False
            try:
                current = self.repository.get_step_record(step.id)
                current_step = self.repository.get_plan(step.plan_id).step(step.id)
                if current_step.status is not StepStatus.PENDING:
                    return False
                ready = current_step.transition_to(StepStatus.READY)
                updated = replace(current, phase=CoordinationPhase.READY)
                self.repository.save_step(
                    step=ready,
                    record=updated,
                    expected_revision=current.revision,
                    claim=step_claim,
                    now=now,
                )
                emit(
                    "coordination.step.ready",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                )
                return True
            finally:
                self.repository.release_claim(step_claim)
        return False

    async def start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
        *,
        claim: ClaimProvider,
        emit: CoordinationEmitter,
    ) -> bool:
        step_claim = claim(step.id, now)
        if step_claim is None:
            return False
        try:
            current = self.repository.get_step_record(step.id)
            current_step = self.repository.get_plan(step.plan_id).step(step.id)
            if (
                current.phase is not CoordinationPhase.READY
                or current_step.status is not StepStatus.READY
            ):
                return False
            attempt = current.current_attempt + 1
            run = await self.kernel.create_run(
                idempotency_key=self._attempt_key(current, attempt),
                task_id=current.task_id,
                subject_type="step",
                subject_id=current.step_id,
                source="platform-coordinator",
            )
            if run.attempt != attempt:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "kernel Run attempt does not match coordinator attempt",
                    details={"expected_attempt": attempt, "run_attempt": run.attempt},
                )
            emit(
                "coordination.attempt.created",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt},
            )
            running = current_step.transition_to(StepStatus.RUNNING)
            updated = replace(
                current,
                phase=CoordinationPhase.ATTEMPT_ACTIVE,
                latest_run_id=run.run_id,
                current_attempt=attempt,
                retry_due_at=None,
                reconciliation=ReconciliationDisposition.CONSISTENT,
                reconciliation_detail=None,
            )
            self.repository.save_step(
                step=running,
                record=updated,
                expected_revision=current.revision,
                claim=step_claim,
                now=now,
            )
            await self.kernel.start_run(
                idempotency_key=self._start_key(current, attempt),
                task_id=current.task_id,
                run_id=run.run_id,
                source="platform-coordinator",
            )
            emit(
                "coordination.attempt.dispatched",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt, "reconciled": False},
            )
            emit(
                "coordination.run.started",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=run.run_id,
                attributes={"attempt": attempt},
            )
            return True
        finally:
            self.repository.release_claim(step_claim)

    @staticmethod
    def _attempt_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}"

    @staticmethod
    def _start_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}:start"
