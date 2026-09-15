from __future__ import annotations

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.execution_budgets import (
    BudgetActionKind,
    BudgetAdmissionOutcome,
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
    TaskBudgetPolicy,
)
from ai_multi_agent_platform.execution_budgets.models import utc_now


@pytest.mark.asyncio
async def test_exhausted_tool_budget_does_not_block_model_only_admission() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    service = TaskBudgetEnforcementService(InMemoryTaskBudgetStore(), accounting)
    await service.put_policy(
        TaskBudgetPolicy(
            task_id="task-a",
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.TOOL_CALLS,
                    limit=1.0,
                    source=BudgetConsumptionSource.ACCOUNTING,
                    metric_type="capability.invocation.count",
                    unit="count",
                ),
            ),
        )
    )
    accounting.record(
        UsageRecord(
            metric_type="capability.invocation.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="observability",
            quantity=1.0,
            scope=UsageScope(task_id="task-a"),
        )
    )

    model_decision = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.MODEL_CALL,
    )
    tool_decision = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.TOOL_CALL,
        quantities={BudgetDimension.TOOL_CALLS: 1.0},
    )

    assert model_decision.outcome is BudgetAdmissionOutcome.ALLOWED
    assert tool_decision.outcome is BudgetAdmissionOutcome.BLOCKED
    assert tool_decision.blocking_dimension is BudgetDimension.TOOL_CALLS


@pytest.mark.asyncio
async def test_unknown_external_cost_blocks_spending_actions_but_not_replanning() -> None:
    accounting = AccountingService(InMemoryUsageStore())
    service = TaskBudgetEnforcementService(InMemoryTaskBudgetStore(), accounting)
    await service.put_policy(
        TaskBudgetPolicy(
            task_id="task-a",
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.EXTERNAL_COST,
                    limit=5.0,
                    source=BudgetConsumptionSource.ACCOUNTING,
                    metric_type="external.cost.amount",
                    unit="USD",
                ),
            ),
        )
    )

    model_decision = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.MODEL_CALL,
    )
    replan_decision = await service.admit(
        task_id="task-a",
        action=BudgetActionKind.REPLAN,
    )

    assert model_decision.outcome is BudgetAdmissionOutcome.METRIC_UNAVAILABLE
    assert model_decision.blocking_dimension is BudgetDimension.EXTERNAL_COST
    assert replan_decision.outcome is BudgetAdmissionOutcome.ALLOWED
