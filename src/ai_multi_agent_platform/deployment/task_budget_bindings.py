"""Deployment-level bindings for autonomous Task execution budgets."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from importlib import import_module
from types import MethodType
from typing import Any, cast

from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator
from ai_multi_agent_platform.coordination.models import StepCoordinationRecord
from ai_multi_agent_platform.domain import Step
from ai_multi_agent_platform.verification.repair import (
    VerificationRepairExecution,
    VerificationRepairRuntime,
)

_StartAttempt = Callable[[Step, StepCoordinationRecord, datetime], Awaitable[bool]]
_ObserveRun = Callable[..., Awaitable[object]]
_CancelActiveRun = Callable[[StepCoordinationRecord, str], Awaitable[None]]
_CancelPlan = Callable[..., Awaitable[object]]


def _budget_models() -> Any:
    """Load budget model types without creating a deployment -> execution package edge."""

    return import_module("ai_multi_agent_platform.execution.budgets.models")


def _budget_store() -> Any:
    """Load reservation-store types without a static deployment -> execution package edge."""

    return import_module("ai_multi_agent_platform.execution.budgets.store")


class TaskBudgetRepairRuntime:
    """Enforce one shared Task repair budget before a new repair Run can start.

    Verification remains the authority for whether repair is required and for its own policy-level
    repair cap. This decorator only contributes the cross-runtime Task budget. Existing idempotent
    repair executions are returned without consuming another Task-budget unit.
    """

    def __init__(
        self,
        inner: VerificationRepairRuntime,
        budgets: Any,
    ) -> None:
        self._inner = inner
        self._budgets = budgets

    async def start_repair(
        self,
        verification_id: str,
        *,
        idempotency_key: str,
        step_id: str | None = None,
        actor_ref: str | None = None,
    ) -> VerificationRepairExecution:
        request = await self._inner._runtime_verification.get_request(  # noqa: SLF001
            verification_id
        )
        repair_attempt = request.repair_attempt + 1
        canonical_key = f"verification-repair:{verification_id}:{repair_attempt}"
        existing = await self._inner._existing_execution(  # noqa: SLF001
            request.task_id,
            verification_id,
            repair_attempt,
            canonical_key,
        )
        if existing is not None:
            return existing

        budget = _budget_models()
        decision = await self._budgets.admit(
            task_id=request.task_id,
            action=budget.BudgetActionKind.REPAIR,
            quantities={budget.BudgetDimension.REPAIRS: 1.0},
            run_id=request.run_id,
            correlation_id=request.correlation_id,
            causation_id=verification_id,
            provenance={
                "enforcement_point": "verification_repair_runtime",
                "verification_id": verification_id,
                "repair_attempt": repair_attempt,
            },
        )
        await self._budgets.require_permitted(decision)
        try:
            execution = await self._inner.start_repair(
                verification_id,
                idempotency_key=idempotency_key,
                step_id=step_id,
                actor_ref=actor_ref,
            )
        except BaseException:
            await self._budgets.release(decision)
            raise

        post_action = await self._budgets.reconcile(decision)
        await self._budgets.require_permitted(post_action)
        return execution


def as_verification_repair_runtime(runtime: TaskBudgetRepairRuntime) -> VerificationRepairRuntime:
    """Narrow compatibility cast for reviewer code that only calls ``start_repair``."""

    return cast(VerificationRepairRuntime, runtime)


class TaskBudgetCoordinationBindings:
    """Bind shared retry and concurrent-Step budgets to the durable coordinator lifecycle.

    `parallel_steps` is a semaphore-like Task resource, not a cumulative operation count. Its claim
    is therefore created without an expiry and remains active until the corresponding canonical Run
    is observed terminal or cancelled. The durable reservation store makes this restart-safe.

    `retries` is cumulative. A retry unit is reserved before dispatch and reconciled only after the
    retry attempt starts successfully.
    """

    def __init__(
        self,
        coordinator: DurablePlanStepCoordinator,
        budgets: Any,
    ) -> None:
        self._coordinator = coordinator
        self._budgets = budgets
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        self._installed = True
        self._bind_start_attempt()
        self._bind_observe_run()
        self._bind_cancel_active_run()
        self._bind_cancel_plan()

    def _bind_start_attempt(self) -> None:
        coordinator = self._coordinator
        original_start = coordinator._start_attempt  # noqa: SLF001

        async def budgeted_start(
            _coordinator: DurablePlanStepCoordinator,
            step: Step,
            record: StepCoordinationRecord,
            now: datetime,
        ) -> bool:
            return await self._start_with_budget(original_start, step, record, now)

        coordinator._start_attempt = MethodType(  # type: ignore[method-assign]  # noqa: SLF001
            budgeted_start,
            coordinator,
        )

    def _bind_observe_run(self) -> None:
        coordinator = self._coordinator
        original_observe = coordinator.observe_run

        async def budgeted_observe(
            _coordinator: DurablePlanStepCoordinator,
            *,
            task_id: str,
            run_id: str,
            failure_category: str | None = None,
            observation_key: str | None = None,
            now: datetime | None = None,
        ) -> object:
            return await self._observe_with_budget(
                original_observe,
                task_id=task_id,
                run_id=run_id,
                failure_category=failure_category,
                observation_key=observation_key,
                now=now,
            )

        coordinator.observe_run = MethodType(  # type: ignore[method-assign]
            budgeted_observe,
            coordinator,
        )

    def _bind_cancel_active_run(self) -> None:
        coordinator = self._coordinator
        original_cancel_active = coordinator._cancel_active_run  # noqa: SLF001

        async def budgeted_cancel_active(
            _coordinator: DurablePlanStepCoordinator,
            record: StepCoordinationRecord,
            key: str,
        ) -> None:
            await self._cancel_active_with_budget(original_cancel_active, record, key)

        coordinator._cancel_active_run = MethodType(  # type: ignore[method-assign]  # noqa: SLF001
            budgeted_cancel_active,
            coordinator,
        )

    def _bind_cancel_plan(self) -> None:
        coordinator = self._coordinator
        original_cancel_plan = coordinator.cancel_plan

        async def budgeted_cancel_plan(
            _coordinator: DurablePlanStepCoordinator,
            plan_id: str,
            *,
            idempotency_key: str,
            now: datetime | None = None,
        ) -> object:
            return await self._cancel_plan_with_budget(
                original_cancel_plan,
                plan_id,
                idempotency_key=idempotency_key,
                now=now,
            )

        coordinator.cancel_plan = MethodType(  # type: ignore[method-assign]
            budgeted_cancel_plan,
            coordinator,
        )

    async def _start_with_budget(
        self,
        original_start: _StartAttempt,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        budget = _budget_models()
        retry_decision: Any | None = None
        parallel_decision: Any | None = None
        try:
            if record.current_attempt > 0:
                retry_decision = await self._budgets.admit(
                    task_id=record.task_id,
                    action=budget.BudgetActionKind.RETRY,
                    quantities={budget.BudgetDimension.RETRIES: 1.0},
                    step_id=record.step_id,
                    correlation_id=record.correlation_id or record.task_id,
                    causation_id=record.latest_run_id,
                    provenance={
                        "enforcement_point": "plan_step_retry",
                        "plan_id": record.plan_id,
                        "attempt": record.current_attempt + 1,
                    },
                )
                await self._budgets.require_permitted(retry_decision)

            parallel_decision = await self._claim_parallel(record)
            started = await original_start(step, record, now)
            if not started:
                await self._release_start_claims(retry_decision, parallel_decision)
                return False

            if retry_decision is not None:
                retry_result = await self._budgets.reconcile(retry_decision)
                await self._budgets.require_permitted(retry_result)
            return True
        except BaseException:
            await self._release_start_claims(retry_decision, parallel_decision)
            raise

    async def _release_start_claims(
        self,
        retry_decision: Any | None,
        parallel_decision: Any | None,
    ) -> None:
        if retry_decision is not None:
            await self._budgets.release(retry_decision)
        if parallel_decision is not None:
            await self._budgets.release(parallel_decision)

    async def _observe_with_budget(
        self,
        original_observe: _ObserveRun,
        *,
        task_id: str,
        run_id: str,
        failure_category: str | None,
        observation_key: str | None,
        now: datetime | None,
    ) -> object:
        step_id = await self._step_for_run(task_id, run_id)
        try:
            return await original_observe(
                task_id=task_id,
                run_id=run_id,
                failure_category=failure_category,
                observation_key=observation_key,
                now=now,
            )
        finally:
            if step_id is not None:
                await self._release_parallel(task_id, step_id)

    async def _cancel_active_with_budget(
        self,
        original_cancel_active: _CancelActiveRun,
        record: StepCoordinationRecord,
        key: str,
    ) -> None:
        try:
            await original_cancel_active(record, key)
        finally:
            await self._release_parallel(record.task_id, record.step_id)

    async def _cancel_plan_with_budget(
        self,
        original_cancel_plan: _CancelPlan,
        plan_id: str,
        *,
        idempotency_key: str,
        now: datetime | None,
    ) -> object:
        state, records = await self._coordinator.runtime_repository.get_plan_snapshot(plan_id)
        try:
            return await original_cancel_plan(
                plan_id,
                idempotency_key=idempotency_key,
                now=now,
            )
        finally:
            for record in records:
                await self._release_parallel(state.plan.task_id, record.step_id)

    async def _claim_parallel(
        self,
        record: StepCoordinationRecord,
    ) -> Any | None:
        budget = _budget_models()
        precheck = await self._budgets.admit(
            task_id=record.task_id,
            action=budget.BudgetActionKind.PARALLEL_STEP,
            step_id=record.step_id,
            correlation_id=record.correlation_id or record.task_id,
            causation_id=record.causation_id,
            provenance={
                "enforcement_point": "plan_step_parallel_precheck",
                "plan_id": record.plan_id,
            },
        )
        await self._budgets.require_permitted(precheck)

        policy = await self._budgets.policy(record.task_id)
        if policy is None:
            return precheck
        limit = policy.limit_for(budget.BudgetDimension.PARALLEL_STEPS)
        if limit is None:
            return precheck
        if limit.source is budget.BudgetConsumptionSource.CLOCK:
            raise ValueError("parallel_steps budget cannot use clock consumption")

        snapshot = await self._budgets.snapshot(record.task_id)
        dimension = snapshot.for_dimension(budget.BudgetDimension.PARALLEL_STEPS)
        if dimension is None:
            return precheck
        reservation = budget.BudgetReservation(
            task_id=record.task_id,
            dimension=budget.BudgetDimension.PARALLEL_STEPS,
            quantity=1.0,
            action=budget.BudgetActionKind.PARALLEL_STEP,
            step_id=record.step_id,
            correlation_id=record.correlation_id or record.task_id,
            causation_id=record.causation_id,
            expires_at=None,
            provenance={
                "enforcement_point": "plan_step_parallel",
                "plan_id": record.plan_id,
                "attempt": record.current_attempt + 1,
            },
        )
        claim = _budget_store().ReservationClaim(
            reservation=reservation,
            limit=limit.limit,
            external_consumed=dimension.consumed,
            include_runtime_counter=False,
        )
        store = self._budgets._store  # noqa: SLF001 - deployment composition seam
        accepted = await asyncio.to_thread(store.try_reserve_many, (claim,))
        if not accepted:
            blocked = budget.BudgetAdmissionDecision(
                task_id=record.task_id,
                action=budget.BudgetActionKind.PARALLEL_STEP,
                outcome=budget.BudgetAdmissionOutcome.BLOCKED,
                reason="Task parallel-Step budget is exhausted",
                blocking_dimension=budget.BudgetDimension.PARALLEL_STEPS,
                snapshot=await self._budgets.snapshot(record.task_id),
            )
            await self._budgets.require_permitted(blocked)
        return budget.BudgetAdmissionDecision(
            task_id=record.task_id,
            action=budget.BudgetActionKind.PARALLEL_STEP,
            outcome=budget.BudgetAdmissionOutcome.ALLOWED,
            reason="Task parallel-Step budget admitted",
            reservations=(reservation,),
            snapshot=await self._budgets.snapshot(record.task_id),
        )

    async def _release_parallel(self, task_id: str, step_id: str) -> None:
        budget = _budget_models()
        store = self._budgets._store  # noqa: SLF001 - deployment composition seam
        reservations = await asyncio.to_thread(store.list_reservations, task_id)
        for reservation in reservations:
            if (
                reservation.dimension is budget.BudgetDimension.PARALLEL_STEPS
                and reservation.step_id == step_id
                and reservation.state is budget.ReservationState.ACTIVE
            ):
                await asyncio.to_thread(store.release_reservation, reservation.id)

    async def _step_for_run(self, task_id: str, run_id: str) -> str | None:
        for state in await self._coordinator.runtime_repository.list_active_plans():
            if state.plan.task_id != task_id:
                continue
            records = await self._coordinator.runtime_repository.list_step_records(state.plan.id)
            for record in records:
                if record.latest_run_id == run_id:
                    return record.step_id
        return None


__all__ = [
    "TaskBudgetCoordinationBindings",
    "TaskBudgetRepairRuntime",
    "as_verification_repair_runtime",
]
