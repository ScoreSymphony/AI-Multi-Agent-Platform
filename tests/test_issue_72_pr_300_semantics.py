from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    RequestContext,
)
from ai_multi_agent_platform.control_plane.conversation_response_streaming import (
    stream_conversation_response,
)
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
    ModelRuntimeConversationResponseProvider,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    ModelRuntime,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
)

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-pr-300",
    owner_type="user",
    owner_id="issue-72-pr-300",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


async def _collect(
    stream: AsyncIterator[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    return [item async for item in stream]


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="assistant",
        instructions=AgentInstructions(
            role=InstructionSource(content="Answer through canonical chat.", version="1")
        ),
        model=AgentModelPolicy(requirements=RoutingRequirements()),
    )


def _stack(
    tmp_path: Path,
) -> tuple[
    AgentService,
    ConversationService,
    ControlPlane,
    ControlPlaneHTTP,
    ModelRegistry,
]:
    agents = AgentService(InMemoryAgentRepository())
    conversations = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    models = ModelRegistry()
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        model_registry=models,
        conversation_service=conversations,
        conversation_agent_service=agents,
    )
    return agents, conversations, control_plane, ControlPlaneHTTP(control_plane), models


def _post(
    http: ControlPlaneHTTP,
    path: str,
    body: dict[str, JsonValue],
    *,
    key: str,
) -> dict[str, JsonValue]:
    response = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=path,
                headers={"content-type": "application/json", "idempotency-key": key},
                body=body,
                trusted_actor=ACTOR,
            )
        )
    )
    assert response.status in {200, 201}, response.body
    return cast(dict[str, JsonValue], response.body)


def test_conversation_creation_pins_omitted_agent_and_team_revisions(tmp_path: Path) -> None:
    agents, conversations, _, http, _ = _stack(tmp_path)
    owner = OwnerRef(type="user", id=ACTOR.owner_id)
    agent = agents.create_agent(_profile("Pinned Agent"), owner_ref=owner)
    teammate = agents.create_agent(_profile("Pinned Teammate"), owner_ref=owner)
    team = agents.create_team(
        AgentTeamProfile(
            name="Pinned Team",
            members=(
                AgentTeamMember(
                    AgentRevisionRef(agent.agent_id, agent.revision),
                    role="lead",
                ),
                AgentTeamMember(
                    AgentRevisionRef(teammate.agent_id, teammate.revision),
                    role="member",
                ),
            ),
            leader_agent_id=agent.agent_id,
        ),
        owner_ref=owner,
    )

    agent_conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Pinned Agent conversation",
            "target": {"kind": "agent", "id": agent.agent_id},
        },
        key="pin-agent",
    )
    team_conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Pinned Team conversation",
            "target": {"kind": "agent_team", "id": team.team_id},
        },
        key="pin-team",
    )

    agent_target = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], agent_conversation["metadata"])["target"],
    )
    team_target = cast(
        dict[str, JsonValue],
        cast(dict[str, JsonValue], team_conversation["metadata"])["target"],
    )
    assert agent_target["revision"] == agent.revision
    assert cast(dict[str, JsonValue], agent_conversation["default_agent"])["revision"] == (
        agent.revision
    )
    assert team_target["revision"] == team.revision
    assert cast(dict[str, JsonValue], team_conversation["default_agent"])["revision"] == (
        team.revision
    )

    agents.update_agent(
        agent.agent_id,
        _profile("Updated Agent"),
        expected_revision=agent.revision,
    )
    persisted = _run(conversations.get_conversation(cast(str, agent_conversation["id"])))
    assert persisted.default_agent is not None
    assert persisted.default_agent.revision == agent.revision
    persisted_target = cast(dict[str, JsonValue], persisted.metadata["target"])
    assert persisted_target["revision"] == agent.revision


def test_authenticated_operation_context_reaches_model_runtime(tmp_path: Path) -> None:
    agents, conversations, control_plane, _, models = _stack(tmp_path)
    provider = FakeModelProvider(response_text="Operation context preserved")
    models.register_provider(provider)
    models.register_model(
        ModelConfiguration(
            config_id="model-operation-context",
            display_name="Operation Context Model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=16_384, modalities=("text",)),
            health=HealthStatus.HEALTHY,
        )
    )
    responder = ModelRuntimeConversationResponseProvider(ModelRuntime(models), agents)
    conversation = _run(
        conversations.create_conversation(
            title="Operation context",
            owner_ref=ACTOR.principal_ref,
        )
    )
    message = _run(
        conversations.append_message(
            conversation_id=conversation.id,
            sender_ref=ACTOR.principal_ref,
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Preserve my request context."),),
        )
    )
    context = RequestContext(
        request_id="request-operation-context",
        correlation_id="correlation-operation-context",
        idempotency_key="response-operation-context",
        actor=ACTOR,
    )

    stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            responder,
            context,
            message.id,
        )
    )
    events = _run(_collect(stream))

    assert events[-1]["type"] == "conversation.response.committed"
    assert len(provider.calls) == 1
    operation = provider.calls[0].context
    assert operation.correlation_id == context.correlation_id
    assert operation.causation_id == context.idempotency_key
    assert operation.owner_type == ACTOR.owner_type
    assert operation.owner_id == ACTOR.owner_id
    assert operation.project_id is None
    assert operation.control is not None
    assert operation.control.idempotency_key == context.idempotency_key
