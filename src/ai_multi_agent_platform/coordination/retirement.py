"""Durable superseded-Plan retirement for #439 on the #384 coordination boundary.

Planning decides which immutable Plan becomes canonical. The coordinator remains the only owner of
Step progression and therefore owns the matching rule that an older Plan must stop producing new
Step attempts once ``Task.plan_ref`` moves to a replacement Plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Plan, RunStatus, Step, StepStatus, validate_id

from .models import (
    CoordinationPhase,
    PlanCoordinationProjection,
    PlanRuntimeState,
    PredecessorFailurePolicy,
    ReconciliationDisposition,
    RetryState,
    StepCoordinationRecord,
    StepRetryPolicy,
    WaitResolution,
)
from .repository import InMemoryCoordinatorRepository as _BaseInMemoryCoordinatorRepository
from .service import DurablePlanStepCoordinator as _BaseDurablePlanStepCoordinator

_TERMINAL_RUNS = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.TIMED_OUT}
)
_RETIREMENT_REASON = "canonical_plan_superseded"


@dataclass(frozen=True, slots=True)
class PlanRetirement:
    """Durable evidence that one coordination Plan must never dispatch again."""

    plan_id: str
    task_id: str
    superseded_by_plan_id: str | None
    reason: str
    retired_at: datetime

    def __post_init__(self) -> None:
        validate_id(self.plan_id, "plan")
        validate_id(self.task_id, "task")
        if self.superseded_by_plan_id is not None:
            validate_id(self.superseded_by_plan_id, "plan")
        if not self.reason.strip():
            raise ValueError("plan retirement reason must not be blank")
        if self.retired_at.tzinfo is None:
            raise ValueError("plan retirement time must be timezone-aware")


class PlanRetirementRepository(Protocol):
    """Narrow durable seam needed by supersession-aware coordination."""

    def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement: ...

    def plan_retirement(self, plan_id: str) -> PlanRetirement | None: ...


class InMemoryCoordinatorRepository(_BaseInMemoryCoordinatorRepository):
    """Reference coordinator store with durable-for-process Plan retirement state."""

    def __init__(self) -> None:
        super().__init__()
        self._plan_retirements: dict[str, PlanRetirement] = {}

    def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement:
        state = self.get_plan(plan_id)
        candidate = PlanRetirement(
            plan_id=plan_id,
            task_id=state.plan.task_id,
            superseded_by_plan_id=superseded_by_plan_id,
            reason=reason,
            retired_at=retired_at,
        )
        with self._lock:
            existing = self._plan_retirements.get(plan_id)
            if existing is not None:
                if (
                    existing.superseded_by_plan_id == candidate.superseded_by_plan_id
                    and existing.reason == candidate.reason
                ):
                    return existing
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"coordination Plan {plan_id} already has a different retirement",
                )
            self._plan_retirements[plan_id] = candidate
            return candidate

    def plan_retirement(self, plan_id: str) -> PlanRetirement | None:
        self.get_plan(plan_id)
        with self._lock:
            return self._plan_retirements.get(plan_id)

    def list_active_plans(self) -> tuple[PlanRuntimeState, ...]:
        states = super().list_active_plans()
        with self._lock:
            retired = frozenset(self._plan_retirements)
        return tuple(state for state in states if state.plan.id not in retired)


class DurablePlanStepCoordinator(_BaseDurablePlanStepCoordinator):
    """#384 coordinator that retires a Plan once canonical ``Task.plan_ref`` moves on.

    Completed historical Steps are preserved. Work that never began is cancelled locally in the
    coordination projection, scheduled retries/waits are closed, and the retired Plan is excluded
    from future ``reconcile_all``/``process_due`` passes. No Task cancellation is emitted: the
    replacement Plan remains the canonical lifecycle authority.
    """

    async def register_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        *,
        retry_policies: dict[str, StepRetryPolicy] | None = None,
        predecessor_failure_policy: PredecessorFailurePolicy = PredecessorFailurePolicy.FAIL_FAST,
    ) -> PlanCoordinationProjection:
        # Retirement is a destructive coordination transition. Repeat the base contract checks
        # first so an invalid replacement can never retire a valid predecessor as a side effect.
        self._validate_graph(plan, steps)
        task = await self.kernel.get_task(plan.task_id)
        if task.plan_ref != plan.id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "coordinator Plan does not match the active canonical task Plan",
                details={"task_id": plan.task_id, "plan_id": plan.id},
            )
        if set(task.step_ids) != {step.id for step in steps}:
            raise ContractError(
                ErrorCode.CONFLICT,
                "coordinator Steps do not match the active canonical task Plan",
            )
        policies = retry_policies or {}
        unknown = set(policies) - {step.id for step in steps}
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "retry policy references unknown Steps",
            )

        await self._retire_other_task_plans(
            task_id=plan.task_id,
            active_plan_id=plan.id,
            now=self._now(None),
        )
        return await super().register_plan(
            plan,
            steps,
            retry_policies=retry_policies,
            predecessor_failure_policy=predecessor_failure_policy,
        )

    async def advance(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        current_time = self._now(now)
        state = self.repository.get_plan(plan_id)
        if await self._retire_if_superseded(state, current_time):
            return self.projection(plan_id)
        result = await super().advance(plan_id, now=current_time)
        # Close the narrow race where canonical planning wins after the first authority check but
        # before a READY Step reaches ``create_run``.
        state = self.repository.get_plan(plan_id)
        if await self._retire_if_superseded(state, current_time):
            return self.projection(plan_id)
        return result

    async def process_due(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[PlanCoordinationProjection, ...]:
        current_time = self._now(now)
        for state in self.repository.list_active_plans():
            await self._retire_if_superseded(state, current_time)
        return await super().process_due(now=current_time)

    async def reconcile_plan(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        current_time = self._now(now)
        state = self.repository.get_plan(plan_id)
        if await self._retire_if_superseded(state, current_time):
            return self.projection(plan_id)
        return await super().reconcile_plan(plan_id, now=current_time)

    async def _start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        task = await self.kernel.get_task(record.task_id)
        if task.plan_ref != record.plan_id:
            await self._retire_plan(
                self.repository.get_plan(record.plan_id),
                superseded_by_plan_id=task.plan_ref,
                now=now,
            )
            return False
        try:
            return await super()._start_attempt(step, record, now)
        except ContractError as exc:
            if exc.code is not ErrorCode.CONFLICT:
                raise
            refreshed = await self.kernel.get_task(record.task_id)
            if refreshed.plan_ref == record.plan_id:
                raise
            await self._retire_plan(
                self.repository.get_plan(record.plan_id),
                superseded_by_plan_id=refreshed.plan_ref,
                now=now,
            )
            return False

    async def _resolve_wait(
        self,
        step_id: str,
        resolution: WaitResolution,
        resolution_key: str,
        now: datetime,
    ) -> PlanCoordinationProjection:
        record = self.repository.get_step_record(step_id)
        state = self.repository.get_plan(record.plan_id)
        if await self._retire_if_superseded(state, now):
            raise ContractError(
                ErrorCode.CONFLICT,
                "cannot resolve a wait owned by a superseded Plan",
                details={"plan_id": record.plan_id, "step_id": step_id},
            )
        return await super()._resolve_wait(step_id, resolution, resolution_key, now)

    async def _retire_other_task_plans(
        self,
        *,
        task_id: str,
        active_plan_id: str,
        now: datetime,
    ) -> None:
        for state in self.repository.list_active_plans():
            if state.plan.task_id != task_id or state.plan.id == active_plan_id:
                continue
            await self._retire_plan(
                state,
                superseded_by_plan_id=active_plan_id,
                now=now,
            )

    async def _retire_if_superseded(
        self,
        state: PlanRuntimeState,
        now: datetime,
    ) -> bool:
        task = await self.kernel.get_task(state.plan.task_id)
        if task.plan_ref == state.plan.id:
            return False
        await self._retire_plan(
            state,
            superseded_by_plan_id=task.plan_ref,
            now=now,
        )
        return True

    async def _retire_plan(
        self,
        state: PlanRuntimeState,
        *,
        superseded_by_plan_id: str | None,
        now: datetime,
    ) -> PlanRetirement:
        repository = cast(PlanRetirementRepository, self.repository)
        existing = repository.plan_retirement(state.plan.id)
        if existing is not None:
            return existing

        for record in self.repository.list_step_records(state.plan.id):
            if record.phase is CoordinationPhase.TERMINAL:
                continue
            claim = self._claim(record.step_id, now)
            if claim is None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "superseded Plan retirement is waiting for an in-flight coordinator claim",
                    details={"plan_id": state.plan.id, "step_id": record.step_id},
                )
            try:
                current = self.repository.get_step_record(record.step_id)
                current_step = self.repository.get_plan(state.plan.id).step(record.step_id)
                if current.phase is CoordinationPhase.TERMINAL:
                    continue
                retired_step, retired_record = await self._retired_step_state(
                    current_step,
                    current,
                    superseded_by_plan_id=superseded_by_plan_id,
                    now=now,
                )
                self.repository.save_step(
                    step=retired_step,
                    record=retired_record,
                    expected_revision=current.revision,
                    claim=claim,
                    now=now,
                )
                self._emit(
                    "coordination.step.superseded",
                    current.task_id,
                    current.plan_id,
                    current.step_id,
                    run_id=current.latest_run_id,
                    attributes={
                        "superseded_by_plan_id": superseded_by_plan_id,
                        "previous_phase": current.phase.value,
                        "previous_status": current_step.status.value,
                    },
                )
            finally:
                self.repository.release_claim(claim)

        retirement = repository.retire_plan(
            state.plan.id,
            superseded_by_plan_id=superseded_by_plan_id,
            reason=_RETIREMENT_REASON,
            retired_at=now,
        )
        self._emit(
            "coordination.plan.superseded",
            state.plan.task_id,
            state.plan.id,
            None,
            attributes={"superseded_by_plan_id": superseded_by_plan_id},
        )
        return retirement

    async def _retired_step_state(
        self,
        step: Step,
        record: StepCoordinationRecord,
        *,
        superseded_by_plan_id: str | None,
        now: datetime,
    ) -> tuple[Step, StepCoordinationRecord]:
        detail = (
            "coordination Plan superseded"
            if superseded_by_plan_id is None
            else f"coordination Plan superseded by {superseded_by_plan_id}"
        )
        retry_state = record.retry_state
        wait = record.wait
        if wait is not None and not wait.resolved:
            wait = self._close_wait(
                wait,
                resolution=WaitResolution.CANCELLED,
                resolution_key=f"superseded:{record.plan_id}",
                now=now,
            )

        if record.phase is CoordinationPhase.ATTEMPT_ACTIVE and record.latest_run_id is not None:
            run = await self.kernel.get_run(record.task_id, record.latest_run_id)
            if run.status not in _TERMINAL_RUNS:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "cannot retire superseded Plan while its canonical Step Run is active",
                    details={
                        "plan_id": record.plan_id,
                        "step_id": record.step_id,
                        "run_id": run.run_id,
                        "run_status": run.status.value,
                    },
                )
            if step.status is StepStatus.RUNNING:
                if run.status is RunStatus.SUCCEEDED:
                    step = step.transition_to(StepStatus.SUCCEEDED)
                    if retry_state is RetryState.ACTIVE:
                        retry_state = RetryState.COMPLETED
                elif run.status in {RunStatus.FAILED, RunStatus.TIMED_OUT}:
                    step = step.transition_to(StepStatus.FAILED)
                    if retry_state is RetryState.ACTIVE:
                        retry_state = RetryState.CANCELLED
                else:
                    step = step.transition_to(StepStatus.CANCELLED)
                    if retry_state is RetryState.ACTIVE:
                        retry_state = RetryState.CANCELLED
        elif record.phase is CoordinationPhase.RETRY_SCHEDULED:
            # Preserve the already-observed FAILED attempt while cancelling only the future retry.
            retry_state = RetryState.CANCELLED
        else:
            if step.status in {StepStatus.PENDING, StepStatus.READY, StepStatus.WAITING}:
                step = step.transition_to(StepStatus.CANCELLED)
            elif step.status is StepStatus.RUNNING:
                # Missing Run identity is inconsistent, but supersession still forbids future work.
                step = step.transition_to(StepStatus.CANCELLED)
            if retry_state in {RetryState.SCHEDULED, RetryState.ACTIVE}:
                retry_state = RetryState.CANCELLED

        return (
            step,
            replace(
                record,
                phase=CoordinationPhase.TERMINAL,
                retry_due_at=None,
                retry_state=retry_state,
                wait=wait,
                reconciliation=ReconciliationDisposition.CANONICAL_TERMINAL,
                reconciliation_detail=detail,
            ),
        )


__all__ = [
    "DurablePlanStepCoordinator",
    "InMemoryCoordinatorRepository",
    "PlanRetirement",
    "PlanRetirementRepository",
]
