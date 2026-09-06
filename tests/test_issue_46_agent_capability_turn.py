from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import AgentCapabilityTurn
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
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
                                "arguments": {"message": "hello from model"},
                            }
                        ],
                    },
                ),
            ),
        )


def test_agent_model_tool_call_executes_pinned_capability_through_invoker() -> None:
    async def scenario() -> None:
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(NativeEchoProvider())

        provider = _ToolCallingModel(model_ref="provider-native-tool-model")
        models = ModelRegistry()
        models.register_provider(provider)
        models.register_model(
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

        task_id = new_id("task")
        run_id = new_id("run")
        agent_id = new_id("agent")
        project_id = new_id("project")
        turn = AgentCapabilityTurn(
            ModelRuntime(models),
            capabilities,
            CapabilityInvoker(capabilities),
        )

        result = await turn.execute(
            task_id=task_id,
            run_id=run_id,
            agent_id=agent_id,
            model_config_id="model-tool-capable",
            instruction="Use the supplied capability.",
            objective="Echo the requested text.",
            capability_ids=(ECHO_CAPABILITY_ID,),
            capability_versions={ECHO_CAPABILITY_ID: "1.0"},
            context=OperationContext(
                correlation_id=task_id,
                owner_type="user",
                owner_id="agent-capability-owner",
                project_id=project_id,
            ),
        )

        assert result.model_ref == "model-tool-capable"
        assert result.model_call_refs == (f"{run_id}:model",)
        assert result.tool_invocation_refs == (f"{run_id}:call-echo",)
        assert result.artifact_refs == ()
        assert result.model_usage == {"total_tokens": 7}
        assert len(result.capability_results) == 1
        capability_result = result.capability_results[0]
        assert capability_result["capability_id"] == ECHO_CAPABILITY_ID
        assert capability_result["capability_version"] == "1.0"
        assert capability_result["status"] == "succeeded"
        assert capability_result["output"] == {"message": "hello from model"}
        assert "hello from model" in result.text

        request = provider.calls[0]
        assert request.requirements["model_config_id"] == "model-tool-capable"
        assert request.requirements["tool_calling"] is True
        assert request.requirements["task_id"] == task_id
        assert request.requirements["run_id"] == run_id
        assert request.requirements["agent_id"] == agent_id

    asyncio.run(scenario())
