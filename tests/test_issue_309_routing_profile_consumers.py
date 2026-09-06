from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    DurableRoutingProfileAgentRuntime,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    JsonModelRoutingProfileRepository,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileService,
    RoutingProfileFallbackPolicy,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider

OWNER = OwnerRef(type="user", id="user-routing-consumer")


def _model_registry() -> ModelRegistry:
    registry = ModelRegistry()
    provider = FakeModelProvider()
    registry.register_provider(provider)
    for config_id, location in (
        ("model-preferred-r1", ModelLocation.LOCAL),
        ("model-preferred-r2", ModelLocation.LOCAL),
        ("model-self-hosted", ModelLocation.SELF_HOSTED),
        ("model-remote", ModelLocation.REMOTE),
    ):
        registry.register_model(
            ModelConfiguration(
                config_id=config_id,
                display_name=config_id,
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=32_768, tool_calling=True),
                location=location,
                health=HealthStatus.HEALTHY,
            )
        )
    return registry


def _agent_profile(routing_profile_ref: str) -> AgentProfile:
    return AgentProfile(
        name="Durable routing consumer",
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use the canonical model policy.")
        ),
        model=AgentModelPolicy(routing_profile_ref=routing_profile_ref),
    )


@pytest.mark.asyncio
async def test_agent_runtime_consumes_exact_persisted_profile_revision(tmp_path) -> None:
    project_id = new_id("project")
    profiles = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    profile_service = ModelRoutingProfileService(profiles)
    context = OperationContext(
        correlation_id="corr-routing-consumer",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )
    first = await profile_service.create_profile(
        name="Pinned routing",
        policy=ModelRoutingProfilePolicy(
            preferred_model_ids=("model-preferred-r1",),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=context,
        project_id=project_id,
    )
    await profile_service.version_profile(
        first.profile_id,
        name="Current routing",
        policy=ModelRoutingProfilePolicy(
            preferred_model_ids=("model-preferred-r2",),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        expected_revision=1,
        principal_ref=OWNER.id,
        context=context,
    )

    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile(first.ref.canonical_ref),
        owner_ref=OWNER,
        project_id=project_id,
    )
    runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=profiles,
        model_registry=_model_registry(),
    )

    prepared = runtime.prepare_agent(
        task_id=new_id("task"),
        run_id=new_id("run"),
        agent_id=agent.agent_id,
    )

    assert prepared.selected_model_config_id == "model-preferred-r1"
    assert profiles.get_definition(first.profile_id).current_revision == 2


@pytest.mark.asyncio
async def test_disabled_profile_fails_before_agent_execution(tmp_path) -> None:
    profiles = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    profile_service = ModelRoutingProfileService(profiles)
    profile = await profile_service.create_profile(
        name="Disabled",
        policy=ModelRoutingProfilePolicy(
            preferred_model_ids=("model-preferred-r1",),
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=OperationContext(
            correlation_id="corr-disabled-profile",
            owner_type=OWNER.type,
            owner_id=OWNER.id,
        ),
    )
    profiles.set_enabled(profile.profile_id, False)

    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile(profile.ref.canonical_ref),
        owner_ref=OWNER,
    )
    runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=profiles,
        model_registry=_model_registry(),
    )

    with pytest.raises(ContractError) as caught:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    assert caught.value.code is ErrorCode.UNAVAILABLE


@pytest.mark.asyncio
async def test_self_hosted_profile_constraint_excludes_remote_but_allows_local(tmp_path) -> None:
    profiles = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    profile_service = ModelRoutingProfileService(profiles)
    profile = await profile_service.create_profile(
        name="Self-hosted only",
        policy=ModelRoutingProfilePolicy(
            requirements=RoutingRequirements(self_hosted_only=True, tool_calling=True),
            preferred_model_ids=(
                "model-remote",
                "model-preferred-r1",
                "model-self-hosted",
            ),
            fallback=RoutingProfileFallbackPolicy.FAIL,
        ),
        owner_ref=OWNER,
        principal_ref=OWNER.id,
        context=OperationContext(
            correlation_id="corr-self-hosted-profile",
            owner_type=OWNER.type,
            owner_id=OWNER.id,
        ),
    )

    route = DeterministicModelRouter(_model_registry()).route_profile(profile)
    assert route.model_config_id == "model-preferred-r1"


def test_exact_profile_resolver_rejects_mutable_legacy_reference(tmp_path) -> None:
    profiles = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile("legacy-latest-pointer"),
        owner_ref=OWNER,
    )
    runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=profiles,
        model_registry=_model_registry(),
    )

    with pytest.raises(ContractError) as caught:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
