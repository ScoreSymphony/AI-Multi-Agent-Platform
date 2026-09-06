from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentCapabilityTurn,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRunStatus,
    AgentRuntime,
    AgentService,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityInvoker,
    CapabilityRegistry,
    NativeEchoProvider,
    bind_canonical_capability_invocation,
)
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ExecutionRequest,
    ExecutionStatus,
    HealthStatus,
    ModelRequest,
    ModelResponse,
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, Task, new_id, validate_id
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
    RoutingRequirements,
)
from ai_multi_agent_platform.onboarding import FirstRunAgentLifecycleBackend
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeModelProvider


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


class _Tasks:
    def __init__(self, state: TaskState) -> None:
        self.state = state

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.state.task_id
        return self.state


def _model_runtime() -> tuple[ModelRuntime, _ToolCallingModel, ModelRegistry]:
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
    return ModelRuntime(models), provider, models


def _agent_profile() -> AgentProfile:
    return AgentProfile(
        name="Capability Agent",
        role="capability-agent",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use only the supplied canonical capability.")
        ),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(modalities=("text",)),
            allow_task_override=True,
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(ECHO_CAPABILITY_ID,),
            constraints=(
                CapabilityConstraint(
                    capability_id=ECHO_CAPABILITY_ID,
                    required=False,
                    exact_version="1.0",
                ),
            ),
        ),
    )


def test_agent_model_tool_call_executes_pinned_capability_through_invoker() -> None:
    async def scenario() -> None:
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(NativeEchoProvider())
        model_runtime, provider, _ = _model_runtime()

        task_id = new_id("task")
        run_id = new_id("run")
        agent_id = new_id("agent")
        project_id = new_id("project")
        turn = AgentCapabilityTurn(
            model_runtime,
            capabilities,
            CapabilityInvoker(
                capabilities,
                canonical_binding_hook=bind_canonical_capability_invocation,
            ),
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
        assert len(result.tool_invocation_refs) == 1
        tool_invocation_id = result.tool_invocation_refs[0]
        validate_id(tool_invocation_id, "tool_invocation")
        assert result.artifact_refs == ()
        assert result.model_usage == {"total_tokens": 7}
        assert len(result.capability_results) == 1
        capability_result = result.capability_results[0]
        assert capability_result["invocation_id"] == f"{run_id}:capability:1"
        assert capability_result["canonical_tool_invocation_id"] == tool_invocation_id
        assert capability_result["model_tool_call_id"] == "call-echo"
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


def test_bound_agent_run_lazily_composes_and_records_capability_execution() -> None:
    async def scenario() -> None:
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(NativeEchoProvider())
        model_runtime, _, model_registry = _model_runtime()

        owner = OwnerRef(type="user", id="agent-capability-owner")
        project_id = new_id("project")
        service = AgentService(InMemoryAgentRepository())
        agent = service.create_agent(
            _agent_profile(),
            owner_ref=owner,
            project_id=project_id,
        )
        runtime = AgentRuntime(
            service,
            model_registry=model_registry,
            capability_registry=capabilities,
        )

        task_id = new_id("task")
        run_id = new_id("run")
        task = Task(
            id=task_id,
            title="Agent capability lifecycle",
            description="Echo the requested text through the canonical capability.",
            owner_ref=owner,
            project_id=project_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=agent.agent_id,
                    agent_revision=agent.revision,
                    model_config_id="model-tool-capable",
                    capability_ids=(ECHO_CAPABILITY_ID,),
                )
            ),
        )
        lifecycle = FirstRunAgentLifecycleBackend(
            delegate=FakeLifecycleBackend(),
            tasks=_Tasks(TaskState(task=task, revision=1)),
            agents=runtime,
            models=model_runtime,
        )
        context = OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id=owner.id,
            project_id=project_id,
        )

        handle = await lifecycle.start(
            ExecutionRequest(
                run_id=run_id,
                subject_type="task",
                subject_id=task_id,
                context=context,
            )
        )
        snapshot = await lifecycle.get(run_id, context)

        assert handle.run_id == run_id
        assert snapshot.status is ExecutionStatus.SUCCEEDED
        assert snapshot.output["model_ref"] == "model-tool-capable"
        tool_invocation_refs = snapshot.output["tool_invocation_refs"]
        assert isinstance(tool_invocation_refs, list)
        assert len(tool_invocation_refs) == 1
        tool_invocation_id = tool_invocation_refs[0]
        assert isinstance(tool_invocation_id, str)
        validate_id(tool_invocation_id, "tool_invocation")
        capability_results = snapshot.output["capability_results"]
        assert isinstance(capability_results, list)
        assert capability_results[0]["invocation_id"] == f"{run_id}:capability:1"
        assert capability_results[0]["canonical_tool_invocation_id"] == tool_invocation_id
        assert capability_results[0]["model_tool_call_id"] == "call-echo"
        assert capability_results[0]["output"] == {"message": "hello from model"}

        agent_run_id = snapshot.output["agent_run_id"]
        assert isinstance(agent_run_id, str)
        agent_run = service.repository.get_agent_run(agent_run_id)
        assert agent_run.status is AgentRunStatus.SUCCEEDED
        assert agent_run.task_id == task_id
        assert agent_run.run_id == run_id
        assert agent_run.capability_ids == (ECHO_CAPABILITY_ID,)
        assert dict(agent_run.capability_versions) == {ECHO_CAPABILITY_ID: "1.0"}
        assert agent_run.model_call_refs == (f"{run_id}:model",)
        assert agent_run.tool_invocation_refs == (tool_invocation_id,)
        assert agent_run.telemetry["capability_invocation_count"] == 1

    asyncio.run(scenario())
