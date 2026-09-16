from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest

from ai_multi_agent_platform.accounting import AccountingService, InMemoryUsageStore
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator
from ai_multi_agent_platform.coordination.models import CoordinationPhase, StepCoordinationRecord
from ai_multi_agent_platform.deployment.task_budget_bindings import TaskBudgetCoordinationBindings
from ai_multi_agent_platform.domain import OwnerRef, Step, new_id
from ai_multi_agent_platform.execution.budgets import (
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
    TaskBudgetPolicy,
)
from ai_multi_agent_platform.execution.budgets.models import utc_now


class _FakeCoordinator:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.runtime_repository = _UnusedRuntimeRepository()

    async def _start_attempt(
        self,
        step: Step,
        record: StepCoordinationRecord,
        now: datetime,
    ) -> bool:
        del record, now
        self.started.append(step.id)
        return True

    async def observe_run(self, **kwargs):  # type: ignore[no-untyped-def]
        return kwargs

    async def _cancel_active_run(self, record: StepCoordinationRecord, key: str) -> None:
        del record, key

    async def cancel_plan(self, plan_id: str, *, idempotency_key: str, now=None):  # type: ignore[no-untyped-def]
        del idempotency_key, now
        return plan_id


class _UnusedRuntimeRepository:
    async def list_active_plans(self):  # type: ignore[no-untyped-def]
        return ()

    async def list_step_records(self, plan_id: str):  # type: ignore[no-untyped-def]
        del plan_id
        return ()

    async def get_plan_snapshot(self, plan_id: str):  # type: ignore[no-untyped-def]
        raise AssertionError(f"unexpected get_plan_snapshot({plan_id})")


def _record(
    *,
    task_id: str,
    plan_id: str,
    step_id: str,
    current_attempt: int,
) -> StepCoordinationRecord:
    return StepCoordinationRecord(
        task_id=task_id,
        plan_id=plan_id,
        plan_revision=1,
        step_id=step_id,
        phase=CoordinationPhase.READY,
        current_attempt=current_attempt,
        correlation_id=task_id,
    )


@pytest.mark.asyncio
async def test_parallel_claim_blocks_retry_counts_and_cancel_releases() -> None:
    task_id = new_id("task")
    plan_id = new_id("plan")
    first_step_id = new_id("step")
    second_step_id = new_id("step")
    budgets = TaskBudgetEnforcementService(
        InMemoryTaskBudgetStore(),
        AccountingService(InMemoryUsageStore()),
    )
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.PARALLEL_STEPS,
                    limit=1.0,
                    source=BudgetConsumptionSource.RUNTIME_COUNTER,
                ),
                TaskBudgetLimit(
                    dimension=BudgetDimension.RETRIES,
                    limit=1.0,
                    source=BudgetConsumptionSource.RUNTIME_COUNTER,
                ),
            ),
        )
    )
    fake = _FakeCoordinator()
    bindings = TaskBudgetCoordinationBindings(
        cast(DurablePlanStepCoordinator, fake),
        budgets,
    )
    bindings.install()
    owner = OwnerRef(type="user", id="budget-test")
    first_step = Step(plan_id=plan_id, id=first_step_id, title="first", owner_ref=owner)
    second_step = Step(plan_id=plan_id, id=second_step_id, title="second", owner_ref=owner)
    first_record = _record(
        task_id=task_id,
        plan_id=plan_id,
        step_id=first_step_id,
        current_attempt=0,
    )
    retry_record = _record(
        task_id=task_id,
        plan_id=plan_id,
        step_id=second_step_id,
        current_attempt=1,
    )
    now = datetime.now(UTC)

    assert await fake._start_attempt(first_step, first_record, now)
    snapshot = await budgets.snapshot(task_id)
    parallel = snapshot.for_dimension(BudgetDimension.PARALLEL_STEPS)
    assert parallel is not None
    assert parallel.reserved == 1.0

    with pytest.raises(ContractError) as blocked:
        await fake._start_attempt(
            second_step,
            _record(
                task_id=task_id,
                plan_id=plan_id,
                step_id=second_step_id,
                current_attempt=0,
            ),
            now,
        )
    assert blocked.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert blocked.value.details["blocking_dimension"] == BudgetDimension.PARALLEL_STEPS.value
    assert fake.started == [first_step_id]

    await bindings._release_parallel(task_id, first_step_id)  # noqa: SLF001
    assert await fake._start_attempt(second_step, retry_record, now)

    after_retry = await budgets.snapshot(task_id)
    retry = after_retry.for_dimension(BudgetDimension.RETRIES)
    parallel = after_retry.for_dimension(BudgetDimension.PARALLEL_STEPS)
    assert retry is not None
    assert retry.consumed == 1.0
    assert parallel is not None
    assert parallel.reserved == 1.0

    await fake._cancel_active_run(retry_record, "cancel-second-step")
    after_cancel = await budgets.snapshot(task_id)
    parallel = after_cancel.for_dimension(BudgetDimension.PARALLEL_STEPS)
    assert parallel is not None
    assert parallel.reserved == 0.0
