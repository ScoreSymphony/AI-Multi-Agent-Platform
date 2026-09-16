from __future__ import annotations

import pytest

from ai_multi_agent_platform.accounting import AccountingService, InMemoryUsageStore
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    InvocationTrace,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution.budgets import (
    BudgetActionKind,
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    TaskBudgetCapabilityInvoker,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
    TaskBudgetModelRuntime,
    TaskBudgetPolicy,
)
from ai_multi_agent_platform.execution.budgets.models import utc_now
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _budgets() -> TaskBudgetEnforcementService:
    return TaskBudgetEnforcementService(
        InMemoryTaskBudgetStore(),
        AccountingService(InMemoryUsageStore()),
    )


async def _consume_one(
    budgets: TaskBudgetEnforcementService,
    *,
    task_id: str,
    action: BudgetActionKind,
    dimension: BudgetDimension,
) -> None:
    decision = await budgets.admit(
        task_id=task_id,
        action=action,
        quantities={dimension: 1.0},
    )
    assert decision.permitted
    await budgets.reconcile(decision)


def _local_runtime(
    budgets: TaskBudgetEnforcementService, *, model_ref: str
) -> tuple[TaskBudgetModelRuntime, FakeModelProvider]:
    provider = FakeModelProvider(model_ref=model_ref)
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id=model_ref,
            display_name="Budget boundary model",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(context_window=4096),
        )
    )
    return TaskBudgetModelRuntime(ModelRuntime(registry), budgets), provider


@pytest.mark.asyncio
async def test_direct_model_runtime_cannot_bypass_task_model_call_budget() -> None:
    task_id = new_id("task")
    budgets = _budgets()
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.MODEL_CALLS,
                    limit=1.0,
                    source=BudgetConsumptionSource.RUNTIME_COUNTER,
                ),
            ),
        )
    )
    await _consume_one(
        budgets,
        task_id=task_id,
        action=BudgetActionKind.MODEL_CALL,
        dimension=BudgetDimension.MODEL_CALLS,
    )

    runtime, provider = _local_runtime(budgets, model_ref="model-budget-boundary")
    request = ModelRequest(
        request_id="budget-boundary-model",
        messages=("hello",),
        requirements={"task_id": task_id},
        context=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="budget-boundary",
        ),
    )

    with pytest.raises(ContractError) as exc_info:
        await runtime.generate(request)

    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.details["blocking_dimension"] == BudgetDimension.MODEL_CALLS.value
    assert provider.calls == []


@pytest.mark.asyncio
async def test_local_model_without_cost_metric_still_consumes_non_cost_budget() -> None:
    task_id = new_id("task")
    budgets = _budgets()
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.MODEL_CALLS,
                    limit=1.0,
                    source=BudgetConsumptionSource.RUNTIME_COUNTER,
                ),
            ),
        )
    )
    runtime, provider = _local_runtime(budgets, model_ref="local-model-no-cost-budget")
    request = ModelRequest(
        request_id="local-budget-first",
        messages=("hello",),
        requirements={"task_id": task_id},
        context=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="local-budget-test",
        ),
    )

    await runtime.generate(request)
    snapshot = await budgets.snapshot(task_id)
    model_calls = snapshot.for_dimension(BudgetDimension.MODEL_CALLS)
    assert model_calls is not None
    assert model_calls.consumed == 1.0
    assert model_calls.remaining == 0.0
    assert len(provider.calls) == 1

    with pytest.raises(ContractError) as exc_info:
        await runtime.generate(
            ModelRequest(
                request_id="local-budget-second",
                messages=("again",),
                requirements={"task_id": task_id},
                context=request.context,
            )
        )
    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_direct_capability_invoker_cannot_bypass_task_tool_call_budget() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    budgets = _budgets()
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.TOOL_CALLS,
                    limit=1.0,
                    source=BudgetConsumptionSource.RUNTIME_COUNTER,
                ),
            ),
        )
    )
    await _consume_one(
        budgets,
        task_id=task_id,
        action=BudgetActionKind.TOOL_CALL,
        dimension=BudgetDimension.TOOL_CALLS,
    )

    registry = CapabilityRegistry()
    await registry.register_provider(NativeEchoProvider())
    inner = CapabilityInvoker(registry)
    invoker = TaskBudgetCapabilityInvoker(inner, budgets)
    context = OperationContext(
        correlation_id=task_id,
        owner_type="user",
        owner_id="budget-boundary",
        project_id=project_id,
    )
    invocation = CapabilityInvocation(
        invocation_id="budget-boundary-tool",
        capability_id=ECHO_CAPABILITY_ID,
        arguments={"message": "hello"},
        context=context,
        trace=InvocationTrace(
            correlation_id=task_id,
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            project_id=project_id,
        ),
    )

    with pytest.raises(ContractError) as exc_info:
        await invoker.invoke(invocation)

    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.details["blocking_dimension"] == BudgetDimension.TOOL_CALLS.value
