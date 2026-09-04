from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ai_multi_agent_platform.agents import AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.agents.models import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseRequest,
    ConversationResponseTarget,
    MessageRole,
    ModelRoutingPreference,
    ModelRuntimeConversationResponseProvider,
)
from ai_multi_agent_platform.conversations.models import ConversationMessage
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    ModelRuntime,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _registry(response_text: str = "Canonical answer") -> tuple[ModelRegistry, FakeModelProvider]:
    provider = FakeModelProvider(response_text=response_text, model_ref="provider-private-model")
    registry = ModelRegistry()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-chat",
            display_name="Canonical chat model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            health=HealthStatus.HEALTHY,
        )
    )
    return registry, provider


def _agent_profile(name: str, instruction: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="assistant",
        instructions=AgentInstructions(role=InstructionSource(content=instruction, version="1")),
        model=AgentModelPolicy(requirements=RoutingRequirements(explicit_model_id="model-chat")),
    )


def _message(text: str = "Hello") -> ConversationMessage:
    return ConversationMessage(
        conversation_id=new_id("conversation"),
        sender_ref="user:alice",
        role=MessageRole.USER,
        content=(ConversationContentBlock.text_block(text),),
    )


async def _collect(
    stream: AsyncIterator[ConversationResponseChunk],
) -> tuple[ConversationResponseChunk, ...]:
    return tuple(item async for item in stream)


def test_agent_conversation_routes_through_canonical_model_runtime() -> None:
    registry, model_provider = _registry()
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile("Chat Agent", "Answer from the exact canonical Agent revision."),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    source = _message("What can you do?")
    request = ConversationResponseRequest(
        request_id="request-agent-chat",
        correlation_id="correlation-agent-chat",
        actor_ref="user:alice",
        conversation_id=source.conversation_id,
        source_message_id=source.id,
        target=ConversationResponseTarget(
            kind="agent",
            id=agent.agent_id,
            revision=agent.revision,
        ),
        history=(source,),
    )
    responder = ModelRuntimeConversationResponseProvider(ModelRuntime(registry), agents)

    chunks = asyncio.run(_collect(responder.stream_response(request)))

    assert [chunk.kind for chunk in chunks] == [
        ConversationResponseChunkKind.ACTIVITY,
        ConversationResponseChunkKind.TEXT,
    ]
    assert chunks[-1].text == "Canonical answer"
    assert all(chunk.model_config_id == "model-chat" for chunk in chunks)
    assert len(model_provider.calls) == 1
    model_request = model_provider.calls[0]
    assert model_request.requirements["model_config_id"] == "model-chat"
    assert model_request.requirements["agent_id"] == agent.agent_id
    canonical_messages = model_request.requirements["canonical_messages"]
    assert isinstance(canonical_messages, list)
    assert canonical_messages[0]["role"] == "system"
    assert agent.agent_id in str(canonical_messages[0])
    assert "Answer from the exact canonical Agent revision." in str(canonical_messages[0])
    serialized = json.dumps(
        {"messages": model_request.messages, "requirements": model_request.requirements}
    )
    assert "provider-private-model" not in serialized
    assert "session_id" not in serialized


def test_team_conversation_uses_exact_team_leader_revision() -> None:
    registry, model_provider = _registry("Team answer")
    agents = AgentService(InMemoryAgentRepository())
    owner = OwnerRef(type="user", id="alice")
    first = agents.create_agent(_agent_profile("First", "First instruction."), owner_ref=owner)
    leader = agents.create_agent(_agent_profile("Leader", "Leader instruction."), owner_ref=owner)
    team = agents.create_team(
        AgentTeamProfile(
            name="Canonical Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(first.agent_id, first.revision),
                    role="researcher",
                ),
                AgentTeamMember(
                    agent=AgentRevisionRef(leader.agent_id, leader.revision),
                    role="lead",
                ),
            ),
            leader_agent_id=leader.agent_id,
        ),
        owner_ref=owner,
    )
    source = _message("Answer as the team.")
    request = ConversationResponseRequest(
        request_id="request-team-chat",
        correlation_id="correlation-team-chat",
        actor_ref="user:alice",
        conversation_id=source.conversation_id,
        source_message_id=source.id,
        target=ConversationResponseTarget(
            kind="agent_team",
            id=team.team_id,
            revision=team.revision,
        ),
        history=(source,),
    )
    responder = ModelRuntimeConversationResponseProvider(ModelRuntime(registry), agents)

    chunks = asyncio.run(_collect(responder.stream_response(request)))

    assert chunks[-1].text == "Team answer"
    assert len(model_provider.calls) == 1
    system_message = model_provider.calls[0].messages[0]
    assert team.team_id in system_message
    assert f"{leader.agent_id}@{leader.revision}" in system_message
    assert "Leader instruction." in system_message


def test_conversation_model_preference_overrides_agent_model_choice() -> None:
    registry, model_provider = _registry("Override answer")
    registry.register_model(
        ModelConfiguration(
            config_id="model-chat-override",
            display_name="Override chat model",
            provider_id=model_provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=32_768, modalities=("text",)),
            health=HealthStatus.HEALTHY,
            priority=10,
        )
    )
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile("Override Agent", "Use the configured routing policy."),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    source = _message()

    request = ConversationResponseRequest(
        request_id="request-model-override",
        correlation_id="correlation-model-override",
        actor_ref="user:alice",
        conversation_id=source.conversation_id,
        source_message_id=source.id,
        target=ConversationResponseTarget(kind="agent", id=agent.agent_id, revision=agent.revision),
        history=(source,),
        model_preference=ModelRoutingPreference(model_config_id="model-chat-override"),
    )
    responder = ModelRuntimeConversationResponseProvider(ModelRuntime(registry), agents)

    chunks = asyncio.run(_collect(responder.stream_response(request)))

    assert chunks[-1].model_config_id == "model-chat-override"
    assert model_provider.calls[-1].requirements["model_config_id"] == "model-chat-override"
