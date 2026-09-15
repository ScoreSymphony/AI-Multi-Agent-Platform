from __future__ import annotations

import pytest

from ai_multi_agent_platform.accounting.models import (
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.accounting.service import AccountingService
from ai_multi_agent_platform.accounting.store import InMemoryUsageStore
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
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


def _token_service(limit: float) -> tuple[TaskBudgetEnforcementService, AccountingService]:
    accounting = AccountingService(InMemoryUsageStore())
    service = TaskBudgetEnforcementService(InMemoryTaskBudgetStore(), accounting)
    return service, accounting


async def _configure_tokens(service: TaskBudgetEnforcementService, limit: float) -> None:
    await service.put_policy(
        TaskBudgetPolicy(
            task_id="task-token-post-action",
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.MODEL_TOKENS,
                    limit=limit,
                    source=BudgetConsumptionSource.ACCOUNTING,
                    metric_type="model.tokens.total",
                    unit="tokens",
                ),
            ),
        )
    )


def _record_tokens(accounting: AccountingService, quantity: float) -> None:
    accounting.record(
        UsageRecord(
            metric_type="model.tokens.total",
            unit="tokens",
            quality=MeasurementQuality.REPORTED,
            source="observability",
            quantity=quantity,
            scope=UsageScope(task_id="task-token-post-action"),
        )
    )


@pytest.mark.asyncio
async def test_reported_token_overrun_is_explicit_after_model_call() -> None:
    service, accounting = _token_service(10.0)
    await _configure_tokens(service, 10.0)
    admitted = await service.admit(
        task_id="task-token-post-action",
        action=BudgetActionKind.MODEL_CALL,
    )
    assert admitted.permitted is True

    # The provider-reported token usage becomes authoritative only after the model call and is
    # persisted by #76 Accounting. The budget service re-reads that state; it does not copy the
    # response into a second ledger.
    _record_tokens(accounting, 12.0)
    post_action = await service.reconcile(admitted)

    assert post_action.outcome is BudgetAdmissionOutcome.EXHAUSTED_DURING_EXECUTION
    assert post_action.blocking_dimension is BudgetDimension.MODEL_TOKENS
    assert post_action.permitted is False
    with pytest.raises(ContractError) as exc_info:
        await service.require_permitted(post_action)
    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert (
        exc_info.value.details["budget_outcome"]
        == BudgetAdmissionOutcome.EXHAUSTED_DURING_EXECUTION.value
    )


@pytest.mark.asyncio
async def test_exact_token_limit_completes_current_call_and_blocks_next_call() -> None:
    service, accounting = _token_service(10.0)
    await _configure_tokens(service, 10.0)
    admitted = await service.admit(
        task_id="task-token-post-action",
        action=BudgetActionKind.MODEL_CALL,
    )

    _record_tokens(accounting, 10.0)
    post_action = await service.reconcile(admitted)

    assert post_action.permitted is True
    assert post_action.outcome is BudgetAdmissionOutcome.WARNING

    followup = await service.admit(
        task_id="task-token-post-action",
        action=BudgetActionKind.MODEL_CALL,
    )
    assert followup.outcome is BudgetAdmissionOutcome.BLOCKED
    assert followup.blocking_dimension is BudgetDimension.MODEL_TOKENS
