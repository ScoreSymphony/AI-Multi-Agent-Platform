from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_multi_agent_platform.agents import AgentCapabilityTurn
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    OperationContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.execution.budgets import (
    BudgetActionKind,
    BudgetAdmissionDecision,
    BudgetAdmissionOutcome,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.testing import FakeModelProvider


class _CancellingModel(FakeModelProvider):
    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise asyncio.CancelledError


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


class _CancellingInvoker:
    async def invoke(self, invocation: CapabilityInvocation) -> Any:
        del invocation
        raise asyncio.CancelledError


class _RecordingBudgetAdmission:
    def __init__(self) -> None:
        self.admitted: list[BudgetAdmissionDecision] = []
        self.reconciled: list[BudgetAdmissionDecision] = []
        self.released: list[BudgetAdmissionDecision] = []

    async def admit(self, **kwargs: Any) -> BudgetAdmissionDecision:
        decision = BudgetAdmissionDecision(
            task_id=kwargs["task_id"],
            action=kwargs["action"],
            outcome=BudgetAdmissionOutcome.ALLOWED,
            reason="allowed",
        )
        self.admitted.append(decision)
        return decision

    async def reconcile(
        self,
        decision: BudgetAdmissionDecision,
        **kwargs: Any,
    ) -> BudgetAdmissionDecision:
        del kwargs
        self.reconciled.append(decision)
        return decision

    async def release(self, decision: BudgetAdmissionDecision) -> None:
        self.released.append(decision)


def _model_runtime(provider: FakeModelProvider) -> ModelRuntime:
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
    return ModelRuntime(registry)


def _context(task_id: str, project_id: str) -> OperationContext:
    return OperationContext(
        correlation_id=task_id,
        owner_type="user",
        owner_id="boundary-owner",
        project_id=project_id,
    )


@pytest.mark.asyncio
async def test_model_cancellation_propagates_and_releases_budget_once() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    budgets = _RecordingBudgetAdmission()
    capabilities = CapabilityRegistry()
    turn = AgentCapabilityTurn(
        _model_runtime(_CancellingModel(model_ref="cancelling-model")),
        capabilities,
        CapabilityInvoker(capabilities),
        budget_admission=budgets,
    )

    with pytest.raises(asyncio.CancelledError):
        await turn.execute(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            model_config_id="model-tool-capable",
            instruction="Cancel during model execution.",
            objective="Exercise cancellation cleanup.",
            capability_ids=(),
            capability_versions={},
            context=_context(task_id, project_id),
        )

    assert [decision.action for decision in budgets.admitted] == [BudgetActionKind.MODEL_CALL]
    assert budgets.released == budgets.admitted
    assert budgets.reconciled == []


@pytest.mark.asyncio
async def test_capability_cancellation_propagates_and_releases_tool_budget_once() -> None:
    task_id = new_id("task")
    run_id = new_id("run")
    agent_id = new_id("agent")
    project_id = new_id("project")
    budgets = _RecordingBudgetAdmission()
    capabilities = CapabilityRegistry()
    await capabilities.register_provider(NativeEchoProvider())
    turn = AgentCapabilityTurn(
        _model_runtime(_ToolCallingModel(model_ref="tool-calling-model")),
        capabilities,
        _CancellingInvoker(),  # type: ignore[arg-type]
        budget_admission=budgets,
    )

    with pytest.raises(asyncio.CancelledError):
        await turn.execute(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            model_config_id="model-tool-capable",
            instruction="Use the capability.",
            objective="Cancel during capability execution.",
            capability_ids=(ECHO_CAPABILITY_ID,),
            capability_versions={ECHO_CAPABILITY_ID: "1.0"},
            context=_context(task_id, project_id),
        )

    assert [decision.action for decision in budgets.admitted] == [
        BudgetActionKind.MODEL_CALL,
        BudgetActionKind.TOOL_CALL,
    ]
    assert budgets.reconciled == [budgets.admitted[0]]
    assert budgets.released == [budgets.admitted[1]]
