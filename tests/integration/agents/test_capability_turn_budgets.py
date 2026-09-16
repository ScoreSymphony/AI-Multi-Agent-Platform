from __future__ import annotations

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingService,
    InMemoryUsageStore,
    MeasurementQuality,
    UsageRecord,
    UsageScope,
)
from ai_multi_agent_platform.agents import AgentCapabilityTurn
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityInvoker,
    CapabilityRegistry,
    NativeEchoProvider,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    OperationContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution.budgets import (
    BudgetConsumptionSource,
    BudgetDimension,
    InMemoryTaskBudgetStore,
    TaskBudgetEnforcementService,
    TaskBudgetLimit,
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


class _ToolCallingModel(FakeModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        tools = request.requirements.get("canonical_tools")
        assert isinstance(tools, list)
        assert len(tools) == 1
        tool = tools[0]
        assert isinstance(tool, dict)
        tool_name = tool.get("name")
        assert isinstance(tool_name, str)
        return ModelResponse(
            request_id=request.request_id,
            text="",
            model_ref=self.model_ref,
            usage={"total_tokens": 7},
            adapter_metadata=(
                AdapterMetadata(
                    namespace="model-protocol",
                    values={
                        "finish_reason": "tool_call",
                        "tool_calls": [
                            {
                                "call_id": "call-echo",
                                "tool_name": tool_name,
                                "arguments": {"message": "hello"},
                            }
                        ],
                    },
                ),
            ),
        )


def _model_runtime() -> tuple[ModelRuntime, _ToolCallingModel]:
    provider = _ToolCallingModel(model_ref="provider-native-tool-model")
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-tool-capable",
            display_name="Tool capable model",
            provider_id=provider.descriptor.provider_id,
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            capabilities=ModelCapabilities(
                context_window=16_384,
                tool_calling=True,
                structured_output=False,
                streaming=False,
                modalities=("text",),
            ),
        )
    )
    return ModelRuntime(registry), provider


async def _capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    await registry.register_provider(NativeEchoProvider())
    return registry


def _context(task_id: str, project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id=task_id,
        owner_type="user",
        owner_id="budget-owner",
        project_id=project_id,
    )


@pytest.mark.asyncio
async def test_model_budget_blocks_before_provider_invocation() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    accounting = AccountingService(InMemoryUsageStore())
    accounting.record(
        UsageRecord(
            metric_type="model.call.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="observability",
            quantity=1.0,
            scope=UsageScope(task_id=task_id),
        )
    )
    budgets = TaskBudgetEnforcementService(InMemoryTaskBudgetStore(), accounting)
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
            started_at=utc_now(),
            limits=(
                TaskBudgetLimit(
                    dimension=BudgetDimension.MODEL_CALLS,
                    limit=1.0,
                    source=BudgetConsumptionSource.ACCOUNTING,
                    metric_type="model.call.count",
                    unit="count",
                ),
            ),
        )
    )
    model_runtime, provider = _model_runtime()
    capabilities = await _capabilities()
    turn = AgentCapabilityTurn(
        model_runtime,
        capabilities,
        CapabilityInvoker(
            capabilities,
            canonical_binding_hook=bind_canonical_capability_invocation,
        ),
        budget_admission=budgets,
    )

    with pytest.raises(ContractError) as exc_info:
        await turn.execute(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            model_config_id="model-tool-capable",
            instruction="Use the capability.",
            objective="Echo the text.",
            capability_ids=(ECHO_CAPABILITY_ID,),
            capability_versions={ECHO_CAPABILITY_ID: "1.0"},
            context=_context(task_id, project_id),
        )

    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.details["blocking_dimension"] == BudgetDimension.MODEL_CALLS.value
    assert provider.calls == []


@pytest.mark.asyncio
async def test_tool_budget_allows_model_turn_then_blocks_before_capability_invocation() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    accounting = AccountingService(InMemoryUsageStore())
    accounting.record(
        UsageRecord(
            metric_type="capability.invocation.count",
            unit="count",
            quality=MeasurementQuality.MEASURED,
            source="observability",
            quantity=1.0,
            scope=UsageScope(task_id=task_id),
        )
    )
    budgets = TaskBudgetEnforcementService(InMemoryTaskBudgetStore(), accounting)
    await budgets.put_policy(
        TaskBudgetPolicy(
            task_id=task_id,
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
    model_runtime, provider = _model_runtime()
    capabilities = await _capabilities()
    turn = AgentCapabilityTurn(
        model_runtime,
        capabilities,
        CapabilityInvoker(
            capabilities,
            canonical_binding_hook=bind_canonical_capability_invocation,
        ),
        budget_admission=budgets,
    )

    with pytest.raises(ContractError) as exc_info:
        await turn.execute(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            model_config_id="model-tool-capable",
            instruction="Use the capability.",
            objective="Echo the text.",
            capability_ids=(ECHO_CAPABILITY_ID,),
            capability_versions={ECHO_CAPABILITY_ID: "1.0"},
            context=_context(task_id, project_id),
        )

    assert exc_info.value.code is ErrorCode.RESOURCE_EXHAUSTED
    assert exc_info.value.details["blocking_dimension"] == BudgetDimension.TOOL_CALLS.value
    assert len(provider.calls) == 1
