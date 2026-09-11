"""Restart reconciliation of durable coordination against canonical kernel truth."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel.models import RecoveryReport, RunState
from ai_multi_agent_platform.observability import TelemetryOutcome

from .models import (
    CoordinationPhase,
    CoordinatorClaim,
    PlanCoordinationProjection,
    ReconciliationDisposition,
    StepCoordinationRecord,
)
from .repository import CoordinatorRepository

_TERMINAL_RUNS = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)


class ReconciliationKernel(Protocol):
    async def recover_task(self, task_id: str) -> RecoveryReport: ...

    async def get_run(self, task_id: str, run_id: str) -> RunState: ...

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState: ...


class ObserveRun(Protocol):
    async def __call__(
        self,
        *,
        task_id: str,
        run_id: str,
        failure_category: str | None = None,
        observation_key: str | None = None,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection: ...


class ProcessDue(Protocol):
    async def __call__(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[PlanCoordinationProjection, ...]: ...


class AdvancePlan(Protocol):
    async def __call__(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection: ...


class ClaimProvider(Protocol):
    def __call__(self, step_id: str, now: datetime) -> CoordinatorClaim | None: ...


class ReconciliationEmitter(Protocol):
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


class CoordinationReconciliation:
    """Own restart recovery and canonical Run reconciliation for active Step attempts."""

    def __init__(
        self,
        *,
        repository: CoordinatorRepository,
        kernel: ReconciliationKernel,
    ) -> None:
        self.repository = repository
        self.kernel = kernel

    async def reconcile_plan(
        self,
        plan_id: str,
        *,
        now: datetime,
        observe_run: ObserveRun,
        process_due: ProcessDue,
        advance: AdvancePlan,
        claim: ClaimProvider,
        emit: ReconciliationEmitter,
    ) -> str:
        state = self.repository.get_plan(plan_id)
        await self.kernel.recover_task(state.plan.task_id)
        for record in self.repository.list_step_records(plan_id):
            if record.phase is not CoordinationPhase.ATTEMPT_ACTIVE:
                continue
            if record.latest_run_id is None:
                await self.mark_inconsistent(
                    record,
                    "active Step has no canonical Run reference",
                    ReconciliationDisposition.MISSING_CANONICAL_RUN,
                    now,
                    claim=claim,
                    emit=emit,
                )
                continue
            try:
                run = await self.kernel.get_run(record.task_id, record.latest_run_id)
            except ContractError as exc:
                if exc.code is ErrorCode.NOT_FOUND:
                    await self.mark_inconsistent(
                        record,
                        "referenced canonical Run is missing",
                        ReconciliationDisposition.MISSING_CANONICAL_RUN,
                        now,
                        claim=claim,
                        emit=emit,
                    )
                    continue
                raise
            if run.status in _TERMINAL_RUNS:
                await observe_run(
                    task_id=record.task_id,
                    run_id=record.latest_run_id,
                    observation_key=f"reconcile:{record.latest_run_id}:{run.status.value}",
                    now=now,
                )
            elif run.status is RunStatus.QUEUED and not run.recovery_required:
                await self.kernel.start_run(
                    idempotency_key=self.start_key(record, run.attempt),
                    task_id=record.task_id,
                    run_id=run.run_id,
                    source="platform-coordinator",
                )
                emit(
                    "coordination.attempt.dispatched",
                    record.task_id,
                    plan_id,
                    record.step_id,
                    run_id=run.run_id,
                    attributes={"attempt": run.attempt, "reconciled": True},
                )
                emit(
                    "coordination.reconciliation.run_started",
                    record.task_id,
                    plan_id,
                    record.step_id,
                    run_id=run.run_id,
                )
        await process_due(now=now)
        await advance(plan_id, now=now)
        emit("coordination.reconciliation.completed", state.plan.task_id, plan_id, None)
        return plan_id

    async def mark_inconsistent(
        self,
        record: StepCoordinationRecord,
        detail: str,
        disposition: ReconciliationDisposition,
        now: datetime,
        *,
        claim: ClaimProvider,
        emit: ReconciliationEmitter,
    ) -> None:
        step_claim = claim(record.step_id, now)
        if step_claim is None:
            return
        try:
            current = self.repository.get_step_record(record.step_id)
            step = self.repository.get_plan(record.plan_id).step(record.step_id)
            updated = replace(
                current,
                phase=CoordinationPhase.INCONSISTENT,
                reconciliation=disposition,
                reconciliation_detail=detail,
            )
            self.repository.save_step(
                step=step,
                record=updated,
                expected_revision=current.revision,
                claim=step_claim,
                now=now,
            )
            emit(
                "coordination.reconciliation.inconsistent",
                current.task_id,
                current.plan_id,
                current.step_id,
                run_id=current.latest_run_id,
                outcome=TelemetryOutcome.FAILED,
                attributes={"disposition": disposition.value, "detail": detail},
            )
        finally:
            self.repository.release_claim(step_claim)

    @staticmethod
    def start_key(record: StepCoordinationRecord, attempt: int) -> str:
        return f"coord:{record.plan_id}:{record.step_id}:attempt:{attempt}:start"
