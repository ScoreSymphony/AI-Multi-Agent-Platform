from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentCommandHandlers,
    AgentDataAccess,
    AgentExecutionEnvironment,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    OrchestratorMapping,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    JsonValue,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelCapabilities, ModelConfiguration, ModelRegistry
from ai_multi_agent_platform.testing import FakeModelProvider

OWNER = OwnerRef(type="user", id="issue-33-final")
SECURE_CAPABILITY_ID = "tool.secure-agent"


def _instructions() -> AgentInstructions:
    return AgentInstructions(role=InstructionSource(content="Do the assigned work."))


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-issue-33-final",
        correlation_id="correlation-issue-33-final",
        actor=ActorContext(
            principal_ref="user:issue-33-final",
            owner_type="user",
            owner_id="issue-33-final",
        ),
    )


class SecureCapabilityProvider(CapabilityToolProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="secure-agent-provider",
            provider_type="test",
            capabilities=(
                Capability(
                    name=SECURE_CAPABILITY_ID,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=SECURE_CAPABILITY_ID,
                    name="Secure Agent Capability",
                    version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    required_permissions=("workspace.write",),
                    required_worker_capabilities=("secure-worker",),
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="secure.agent",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


class RecordingMapper:
    adapter_id = "recording-orchestrator"

    def __init__(self) -> None:
        self.specs = []

    async def map_agent(self, spec):  # type: ignore[no-untyped-def]
        self.specs.append(spec)
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"recording:{spec.agent_revision.agent_id}:{spec.run_id}",
            metadata={"mapped": True},
        )


class TrustedEnvironmentResolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bool]] = []

    async def resolve(
        self,
        context: RequestContext,
        *,
        resource_ref: str,
        task_id: str,
        run_id: str,
        is_team: bool,
    ) -> AgentExecutionEnvironment:
        assert context.actor.principal_ref == "user:issue-33-final"
        self.calls.append((resource_ref, task_id, run_id, is_team))
        return AgentExecutionEnvironment(
            granted_permissions=frozenset({"workspace.write"}),
            available_worker_capabilities=frozenset({"secure-worker"}),
        )


def test_team_parallel_limit_is_a_scheduler_limit_not_a_member_count_limit() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)
        first = service.create_agent(
            AgentProfile(name="First", role="worker", instructions=_instructions()),
            owner_ref=OWNER,
        )
        second = service.create_agent(
            AgentProfile(name="Second", role="worker", instructions=_instructions()),
            owner_ref=OWNER,
        )
        team = service.create_team(
            AgentTeamProfile(
                name="Limited team",
                members=(
                    AgentTeamMember(AgentRevisionRef(first.agent_id, 1), role="worker"),
                    AgentTeamMember(AgentRevisionRef(second.agent_id, 1), role="worker"),
                ),
                max_parallel_agents=1,
                max_steps=3,
            ),
            owner_ref=OWNER,
        )

        records = await AgentRuntime(service).start_team(
            task_id=new_id("task"),
            run_id=new_id("run"),
            team_id=team.team_id,
        )

        assert len(records) == 2
        for record in records:
            mapping = record.telemetry["orchestrator_mapping"]
            assert isinstance(mapping, dict)
            assert mapping["team_id"] == team.team_id
            assert mapping["team_revision"] == 1
            assert mapping["team_limits"] == {
                "max_parallel_agents": 1,
                "max_steps": 3,
            }

    asyncio.run(scenario())


def test_policy_memory_config_and_team_shared_resource_refs_are_enforceable_hooks() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    agent = service.create_agent(
        AgentProfile(
            name="Policy-bound agent",
            role="researcher",
            instructions=_instructions(),
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.AGENT,),
                memory_config_refs=("memory:research",),
            ),
            policy_hooks=AgentPolicyHooks(
                authorization_profile_ref="authorization:research",
            ),
        ),
        owner_ref=OWNER,
    )
    team = service.create_team(
        AgentTeamProfile(
            name="Shared-memory team",
            members=(AgentTeamMember(AgentRevisionRef(agent.agent_id, 1), role="researcher"),),
            shared_resource_refs=("memory-config:team-shared",),
        ),
        owner_ref=OWNER,
    )

    service.ensure_memory_config(agent.agent_id, 1, "memory:research")
    service.ensure_authorization_profile(agent.agent_id, 1, "authorization:research")
    service.ensure_team_shared_resource(team.team_id, 1, "memory-config:team-shared")

    with pytest.raises(ContractError) as memory_error:
        service.ensure_memory_config(agent.agent_id, 1, "memory:other")
    assert memory_error.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as auth_error:
        service.ensure_authorization_profile(agent.agent_id, 1, "authorization:admin")
    assert auth_error.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as resource_error:
        service.ensure_team_shared_resource(team.team_id, 1, "memory-config:other")
    assert resource_error.value.code is ErrorCode.FORBIDDEN


def test_control_plane_start_maps_full_runtime_context_through_registered_adapter() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)

        model_registry = ModelRegistry()
        model_provider = FakeModelProvider()
        model_registry.register_provider(model_provider)
        model_registry.register_model(
            ModelConfiguration(
                config_id="model-cp-override",
                display_name="Control Plane Override",
                provider_id=model_provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=16_000),
                health=HealthStatus.HEALTHY,
            )
        )

        capability_registry = CapabilityRegistry()
        await capability_registry.register_provider(SecureCapabilityProvider())
        runtime = AgentRuntime(
            service,
            model_registry=model_registry,
            capability_registry=capability_registry,
        )
        agent = service.create_agent(
            AgentProfile(
                name="Control Plane Agent",
                role="worker",
                instructions=_instructions(),
                model=AgentModelPolicy(allow_task_override=True),
                capabilities=AgentCapabilityPolicy(
                    allowed=(SECURE_CAPABILITY_ID,),
                    constraints=(
                        CapabilityConstraint(
                            capability_id=SECURE_CAPABILITY_ID,
                            required=True,
                            exact_version="1.0",
                        ),
                    ),
                ),
            ),
            owner_ref=OWNER,
        )
        mapper = RecordingMapper()
        resolver = TrustedEnvironmentResolver()
        handlers = AgentCommandHandlers(
            service,
            runtime,
            orchestrator_mappers={mapper.adapter_id: mapper},
            execution_environment_resolver=resolver,
        )
        task_id = new_id("task")
        run_id = new_id("run")

        result = await handlers.start_agent(
            _context(),
            agent.agent_id,
            {
                "task_id": task_id,
                "run_id": run_id,
                "orchestrator_adapter_id": mapper.adapter_id,
                "task_model_override": {"explicit_model_id": "model-cp-override"},
                "task_context": {"goal": "complete issue 33"},
                "project_context": {"workspace_instruction": "keep contracts canonical"},
            },
        )

        assert result["selected_model_config_id"] == "model-cp-override"
        assert result["orchestrator_adapter_id"] == mapper.adapter_id
        assert result["capability_versions"] == {SECURE_CAPABILITY_ID: "1.0"}
        assert resolver.calls == [(agent.agent_id, task_id, run_id, False)]
        assert len(mapper.specs) == 1
        spec = mapper.specs[0]
        assert spec.task_context == {"goal": "complete issue 33"}
        assert spec.project_context == {"workspace_instruction": "keep contracts canonical"}
        assert dict(spec.capability_versions) == {SECURE_CAPABILITY_ID: "1.0"}

    asyncio.run(scenario())


def test_control_plane_rejects_caller_asserted_runtime_permissions_and_availability() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        runtime = AgentRuntime(service)
        agent = service.create_agent(
            AgentProfile(name="Safe Agent", role="worker", instructions=_instructions()),
            owner_ref=OWNER,
        )
        handlers = AgentCommandHandlers(service, runtime)

        with pytest.raises(ContractError) as exc_info:
            await handlers.start_agent(
                _context(),
                agent.agent_id,
                {
                    "task_id": new_id("task"),
                    "run_id": new_id("run"),
                    "granted_permissions": ["admin"],
                    "available_capability_ids": ["tool.fake"],
                },
            )

        assert exc_info.value.code is ErrorCode.INVALID_REQUEST
        fields = exc_info.value.details["fields"]
        assert isinstance(fields, list)
        assert fields == ["available_capability_ids", "granted_permissions"]

    asyncio.run(scenario())
