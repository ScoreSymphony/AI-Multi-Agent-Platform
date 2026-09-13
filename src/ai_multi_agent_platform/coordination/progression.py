"""Dependency-barrier progression and canonical Step-attempt dispatch."""

from __future__ import annotations

import asyncio
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

_STALE_STREAM_RETRY_LIMIT = 32
_STALE_STREAM_RETRY_DELAY_SECONDS = 0.001


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

        # A Step may be SKIPPED intentionally after all of its own dependencies were satisfied,
        # or because its predecessor barrier failed. Only the former is a successful prerequisite.
        # Preserve that distinction from canonical coordination records so a skipped fan-in caused
        # by failure propagates transitively instead of incorrectly unblocking downstream work.
        satisfied_items: list[str] = []
        failed_items: list[str] = []
        for dependency_id in record.dependency_ids:
            dependency = by_id[dependency_id]
            if dependency.status is StepStatus.SUCCEEDED:
                satisfied_items.append(dependency_id)
            elif dependency.status in {StepStatus.FAILED, StepStatus.CANCELLED}:
                failed_items.append(dependency_id)
            elif dependency.status is StepStatus.SKIPPED:
                dependency_record = self.repository.get_step_record(dependency_id)
                if set(dependency_record.satisfied_dependency_ids) == set(
                    dependency_record.dependency_ids
                ):
                    satisfied_items.append(dependency_id)
                else:
                    failed_items.append(dependency_id)
        satisfied = tuple(satisfied_items)
        failed = tuple(failed_items)

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
            run = await self._create_run_with_stale_retry(current, attempt)
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
            await self._start_run_with_stale_retry(current, attempt, run.run_id)
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

    async def _create_run_with_stale_retry(
        self,
        record: StepCoordinationRecord,
        attempt: int,
    ) -> RunState:
        last_stale_conflict: ContractError | None = None
        for _ in range(_STALE_STREAM_RETRY_LIMIT):
            try:
                return await self.kernel.create_run(
                    idempotency_key=self._attempt_key(record, attempt),
                    task_id=record.task_id,
                    subject_type="step",
                    subject_id=record.step_id,
                    source="platform-coordinator",
                )
            except ContractError as exc:
                if not _is_stale_revision_conflict(exc):
                    raise
                last_stale_conflict = exc
                await asyncio.sleep(_STALE_STREAM_RETRY_DELAY_SECONDS)
        if last_stale_conflict is None:
            raise RuntimeError("stale-revision create_run retry loop exited without a conflict")
        raise last_stale_conflict

    async def _start_run_with_stale_retry(
        self,
        record: StepCoordinationRecord,
        attempt: int,
        run_id: str,
    ) -> RunState:
        last_stale_conflict: ContractError | None = None
        for _ in range(_STALE_STREAM_RETRY_LIMIT):
            try:
                return await self.kernel.start_run(
                    idempotency_key=self._start_key(record, attempt),
                    task_id=record.task_id,
                    run_id=run_id,
                    source="platform-coordinator",
                )
            except ContractError as exc:
                if not _is_stale_revision_conflict(exc):
                    raise
                last_stale_conflict = exc
                await asyncio.sleep(_STALE_STREAM_RETRY_DELAY_SECONDS)
        if last_stale_conflict is None:
            raise RuntimeError("stale-revision start_run retry loop exited without a conflict")
        raise last_stale_conflict

    @staticmethod
    def _attempt_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}"

    @staticmethod
    def _start_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}:start"


def _is_stale_revision_conflict(exc: ContractError) -> bool:
    return (
        exc.code is ErrorCode.CONFLICT
        and exc.retryable
        and exc.details.get("reason") == "stale_stream_revision"
    )
