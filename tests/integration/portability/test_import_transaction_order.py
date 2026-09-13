from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileService,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.models.routing_profile_assignment_context import (
    RoutingProfileAssignmentAccess,
    activate_routing_profile_assignment_access,
)
from ai_multi_agent_platform.portability.agent_codecs import snapshot_agent
from ai_multi_agent_platform.portability.agent_import import AgentImportMutationHandler
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.routing_profile_reference_codecs import (
    RoutingProfileAwareAgentPortableCodec,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider

_OWNER = OwnerRef(type="user", id="user-issue-445-package")


def test_agent_preflight_allows_profile_created_earlier_in_same_package(tmp_path) -> None:
    profile_id = new_model_routing_profile_id()
    routing_ref = ModelRoutingProfileRef(profile_id, 1).canonical_ref

    source_agents = AgentService(InMemoryAgentRepository())
    source_agent = source_agents.create_agent(
        AgentProfile(
            name="In-package routed Agent",
            role="researcher",
            instructions=AgentInstructions(
                role=InstructionSource(content="Use the imported routing profile.")
            ),
            model=AgentModelPolicy(routing_profile_ref=routing_ref),
        ),
        owner_ref=_OWNER,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareAgentPortableCodec())
    resource = serializers.serialize(
        "agent",
        snapshot_agent(source_agents.repository, source_agent.agent_id),
    )
    context = ImportContext()
    snapshot = serializers.deserialize(resource, context)

    target_agents = InMemoryAgentRepository()
    target_profiles = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    authorization = FakeAuthorizationProvider(allowed=True)
    access = RoutingProfileAssignmentAccess(
        gate=ModelRoutingProfileAssignmentGate(target_profiles, authorization=authorization),
        principal_ref=_OWNER.id,
        actor_type="human",
        correlation_id="corr-issue-445-package",
        causation_id="request-issue-445-package",
    )
    handler = AgentImportMutationHandler(target_agents)

    with activate_routing_profile_assignment_access(access):
        # ImportExecutor preflights every package resource before applying any of them.
        # The referenced routing profile therefore does not exist yet at this point.
        asyncio.run(handler.preflight(resource, snapshot, context))
        assert authorization.calls == []

        # Simulate the routing-profile dependency being applied earlier in import order.
        asyncio.run(
            ModelRoutingProfileService(target_profiles).create_profile(
                name="Imported routing profile",
                policy=ModelRoutingProfilePolicy(),
                owner_ref=_OWNER,
                principal_ref=_OWNER.id,
                context=OperationContext(
                    correlation_id="corr-issue-445-package",
                    owner_type=_OWNER.type,
                    owner_id=_OWNER.id,
                ),
                profile_id=profile_id,
            )
        )

        token = asyncio.run(handler.apply(resource, snapshot, context))

    assert token == source_agent.agent_id
    assert target_agents.get_agent(source_agent.agent_id).agent_id == source_agent.agent_id
    assert len(authorization.calls) == 1
    assert authorization.calls[0].action == "model-routing-profile:assign"
