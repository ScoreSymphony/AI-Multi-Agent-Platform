from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.execution_budgets import (
    BudgetActionKind,
    BudgetAdmissionOutcome,
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    SQLiteTaskBudgetStore,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
    TaskBudgetPolicy,
    UnavailableMetricPolicy,
)
from ai_multi_agent_platform.execution_budgets.models import utc_now


def _service(
    store: InMemoryTaskBudgetStore | SQLiteTaskBudgetStore | None = None,
) -> tuple[TaskBudgetEnforcementService, AccountingService]:
    accounting = AccountingService(InMemoryUsageStore())
    return TaskBudgetEnforcementService(store or InMemoryTaskBudgetStore(), accounting), accounting


def _policy(*limits: TaskBudgetLimit, task_id: str = "task-a") -> TaskBudgetPolicy:
    return TaskBudgetPolicy(task_id=task_id, limits=limits, started_at=utc_now())


@pytest.mark.asyncio
async def test_accounting_backed_model_call_budget_reserves_against_canonical_usage() -> None:
    service, accounting = _service()
    await service.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.MODEL_CALLS,
                limit=2.0,
                source=BudgetConsumptionSource.ACCOUNTING,
                metric_type="model.call.count",
                unit="count",
            )
        )
    )
    accounting.record(
        UsageRecord(
            metric_type="model.call.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="observability",
            quantity=1.0,
            scope=UsageScope(task_id="task-a"),
        )
    )

    decision = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.MODEL_CALL,
        quantities={BudgetDimension.MODEL_CALLS: 1.0},
    )

    assert decision.permitted is True
    assert decision.outcome is BudgetAdmissionOutcome.WARNING
    assert len(decision.reservations) == 1
    snapshot = decision.snapshot
    assert snapshot is not None
    state = snapshot.for_dimension(BudgetDimension.MODEL_CALLS)
    assert state is not None
    assert state.consumed == 1.0
    assert state.reserved == 1.0
    assert state.remaining == 0.0


@pytest.mark.asyncio
async def test_runtime_counter_reconcile_blocks_followup_replan() -> None:
    service, _ = _service()
    await service.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.REPLANS,
                limit=1.0,
                source=BudgetConsumptionSource.RUNTIME_COUNTER,
            )
        )
    )
    first = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.REPLAN,
        quantities={BudgetDimension.REPLANS: 1.0},
    )
    await service.reconcile(first)

    second = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.REPLAN,
        quantities={BudgetDimension.REPLANS: 1.0},
    )

    assert second.outcome is BudgetAdmissionOutcome.BLOCKED
    assert second.blocking_dimension is BudgetDimension.REPLANS
    with pytest.raises(ContractError) as exc_info:
        await service.require_permitted(second)
    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.details["blocking_dimension"] == BudgetDimension.REPLANS.value


@pytest.mark.asyncio
async def test_release_returns_unused_parallel_claim_to_shared_budget() -> None:
    service, _ = _service()
    await service.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.PARALLEL_STEPS,
                limit=1.0,
                source=BudgetConsumptionSource.RUNTIME_COUNTER,
            )
        )
    )
    first = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.PARALLEL_STEP,
        quantities={BudgetDimension.PARALLEL_STEPS: 1.0},
    )
    assert first.permitted is True
    await service.release(first)

    second = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.PARALLEL_STEP,
        quantities={BudgetDimension.PARALLEL_STEPS: 1.0},
    )
    assert second.permitted is True


@pytest.mark.asyncio
async def test_parallel_reservation_race_allows_exactly_one_claim() -> None:
    service, _ = _service()
    await service.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.REPLANS,
                limit=1.0,
                source=BudgetConsumptionSource.RUNTIME_COUNTER,
            )
        )
    )

    async def reserve() -> bool:
        decision = await service.admit(
            task_id="task-a",
            action=BudgetActionKind.REPLAN,
            quantities={BudgetDimension.REPLANS: 1.0},
        )
        return decision.permitted

    outcomes = await asyncio.gather(reserve(), reserve())
    assert sorted(outcomes) == [False, True]


@pytest.mark.asyncio
async def test_runtime_deadline_blocks_new_autonomous_work() -> None:
    service, _ = _service()
    policy = TaskBudgetPolicy(
        task_id="task-a",
        limits=(
            TaskBudgetLimit(
                dimension=BudgetDimension.RUNTIME_SECONDS,
                limit=10.0,
                source=BudgetConsumptionSource.CLOCK,
            ),
        ),
        started_at=utc_now() - timedelta(seconds=11),
    )
    await service.put_policy(policy)

    decision = await service.admit(task_id="task-a", action=BudgetActionKind.MODEL_CALL)

    assert decision.outcome is BudgetAdmissionOutcome.BLOCKED
    assert decision.blocking_dimension is BudgetDimension.RUNTIME_SECONDS


@pytest.mark.asyncio
async def test_explicit_unavailable_cost_blocks_hard_budget_without_fabricating_zero() -> None:
    service, accounting = _service()
    await service.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.EXTERNAL_COST,
                limit=5.0,
                source=BudgetConsumptionSource.ACCOUNTING,
                metric_type="external.cost.amount",
                unit="USD",
                unavailable_policy=UnavailableMetricPolicy.BLOCK,
            )
        )
    )
    accounting.record_unavailable(
        metric_type="external.cost.amount",
        unit="USD",
        source="provider",
        scope=UsageScope(task_id="task-a"),
    )

    decision = await service.admit(task_id="task-a", action=BudgetActionKind.MODEL_CALL)

    assert decision.outcome is BudgetAdmissionOutcome.METRIC_UNAVAILABLE
    assert decision.blocking_dimension is BudgetDimension.EXTERNAL_COST
    snapshot = decision.snapshot
    assert snapshot is not None
    state = snapshot.for_dimension(BudgetDimension.EXTERNAL_COST)
    assert state is not None
    assert state.unavailable_count == 1
    assert state.consumed == 0.0


@pytest.mark.asyncio
async def test_estimated_cost_is_excluded_unless_policy_explicitly_includes_it() -> None:
    service, accounting = _service()
    limit = TaskBudgetLimit(
        dimension=BudgetDimension.EXTERNAL_COST,
        limit=10.0,
        source=BudgetConsumptionSource.ACCOUNTING,
        metric_type="external.cost.amount",
        unit="USD",
        include_estimated=False,
    )
    await service.put_policy(_policy(limit))
    accounting.record(
        UsageRecord(
            metric_type="external.cost.amount",
            unit="USD",
            quality=MeasurementQuality.ESTIMATED,
            source="configured-estimator",
            quantity=9.0,
            scope=UsageScope(task_id="task-a"),
            cost_amount=9.0,
            currency="USD",
        )
    )

    excluded = await service.snapshot("task-a")
    excluded_state = excluded.for_dimension(BudgetDimension.EXTERNAL_COST)
    assert excluded_state is not None
    assert excluded_state.consumed == 0.0
    assert excluded_state.quality_counts[MeasurementQuality.ESTIMATED] == 1

    await service.put_policy(replace(_policy(limit), limits=(replace(limit, include_estimated=True),), version=2))
    included = await service.snapshot("task-a")
    included_state = included.for_dimension(BudgetDimension.EXTERNAL_COST)
    assert included_state is not None
    assert included_state.consumed == 9.0


@pytest.mark.asyncio
async def test_sqlite_restart_preserves_runtime_counter_and_active_reservation(tmp_path) -> None:
    path = tmp_path / "task-budgets.sqlite3"
    first, _ = _service(SQLiteTaskBudgetStore(path))
    await first.put_policy(
        _policy(
            TaskBudgetLimit(
                dimension=BudgetDimension.REPLANS,
                limit=3.0,
                source=BudgetConsumptionSource.RUNTIME_COUNTER,
            )
        )
    )
    consumed = await first.admit(
        task_id="task-a",
        action=BudgetActionKind.REPLAN,
        quantities={BudgetDimension.REPLANS: 1.0},
    )
    await first.reconcile(consumed)
    active = await first.admit(
        task_id="task-a",
        action=BudgetActionKind.REPLAN,
        quantities={BudgetDimension.REPLANS: 1.0},
    )
    assert active.permitted is True

    restarted, _ = _service(SQLiteTaskBudgetStore(path))
    snapshot = await restarted.snapshot("task-a")
    state = snapshot.for_dimension(BudgetDimension.REPLANS)
    assert state is not None
    assert state.consumed == 1.0
    assert state.reserved == 1.0
    assert state.remaining == 1.0


@pytest.mark.asyncio
async def test_policy_version_history_is_restart_safe(tmp_path) -> None:
    path = tmp_path / "task-policy.sqlite3"
    store = SQLiteTaskBudgetStore(path)
    service, _ = _service(store)
    first = _policy(
        TaskBudgetLimit(
            dimension=BudgetDimension.TOOL_CALLS,
            limit=10.0,
            source=BudgetConsumptionSource.ACCOUNTING,
            metric_type="capability.invocation.count",
            unit="count",
        )
    )
    await service.put_policy(first)
    second = replace(
        first,
        limits=(replace(first.limits[0], limit=20.0),),
        version=2,
        updated_at=utc_now(),
    )
    await service.put_policy(second)

    restarted, _ = _service(SQLiteTaskBudgetStore(path))
    history = await restarted.policy_history("task-a")
    assert [item.version for item in history] == [1, 2]
    assert history[-1].limits[0].limit == 20.0
