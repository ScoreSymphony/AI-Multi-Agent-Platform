from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRoutingProfileRef, new_model_routing_profile_id
from ai_multi_agent_platform.portability.agent_codecs import snapshot_agent
from ai_multi_agent_platform.portability.dependencies import parse_resource_dependency
from ai_multi_agent_platform.portability.model_routing_profile_codecs import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
)
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.routing_profile_reference_codecs import (
    RoutingProfileAwareAgentPortableCodec,
)

OWNER = OwnerRef(type="user", id="user-routing-reference-portability")


def test_agent_portability_declares_and_remaps_exact_routing_profile_reference() -> None:
    source_profile_id = new_model_routing_profile_id()
    target_profile_id = new_model_routing_profile_id()
    routing_ref = ModelRoutingProfileRef(source_profile_id, 3).canonical_ref
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        AgentProfile(
            name="Portable routed Agent",
            role="researcher",
            instructions=AgentInstructions(
                role=InstructionSource(content="Use the pinned routing profile.")
            ),
            model=AgentModelPolicy(routing_profile_ref=routing_ref),
        ),
        owner_ref=OWNER,
    )
    registry = ResourceSerializerRegistry()
    registry.register(RoutingProfileAwareAgentPortableCodec())

    resource = registry.serialize("agent", snapshot_agent(agents.repository, agent.agent_id))

    dependencies = [
        (parse_resource_dependency(item), item)
        for item in resource.dependencies
        if item.kind.value == "resource"
    ]
    profile_dependencies = [
        (reference, item)
        for reference, item in dependencies
        if reference.resource_type == MODEL_ROUTING_PROFILE_RESOURCE_TYPE
    ]
    assert len(profile_dependencies) == 1
    reference, dependency = profile_dependencies[0]
    assert reference.resource_id == source_profile_id
    assert dependency.version_constraint == "==3"

    restored = registry.deserialize(
        resource,
        ImportContext(
            id_mapping={
                (MODEL_ROUTING_PROFILE_RESOURCE_TYPE, source_profile_id): target_profile_id,
            }
        ),
    )
    assert restored.revisions[0].profile.model.routing_profile_ref == (
        ModelRoutingProfileRef(target_profile_id, 3).canonical_ref
    )
