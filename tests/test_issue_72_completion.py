from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevisionRef,
    AgentRuntime,
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
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.conversations.model_runtime import (
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
    principal_ref="user:issue-72-completion",
    owner_type="user",
    owner_id="issue-72-completion",
    actor_type="human",
)
MODEL_ID = "model-conversation-canonical"


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


async def _collect(stream: AsyncIterator[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return [item async for item in stream]


def _profile(instruction: str, *, explicit_model: str = MODEL_ID) -> AgentProfile:
    return AgentProfile(
        name="Conversation Agent",
        role="assistant",
        instructions=AgentInstructions(role=InstructionSource(content=instruction, version="1")),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(explicit_model_id=explicit_model),
        ),
    )


def _model_stack(
    tmp_path: Path,
    *,
    response_text: str = "Canonical assistant answer",
) -> tuple[
    FakeModelProvider,
    ModelRegistry,
    AgentService,
    ConversationService,
    ControlPlane,
    ControlPlaneHTTP,
    ModelRuntimeConversationResponseProvider,
]:
    provider = FakeModelProvider(
        response_text=response_text,
        model_ref="provider-private/native-chat-model",
    )
    models = ModelRegistry()
    models.register_provider(provider)
    models.register_model(
        ModelConfiguration(
            config_id=MODEL_ID,
            display_name="Canonical conversation model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(streaming=True),
            health=HealthStatus.HEALTHY,
        )
    )
    agents = AgentService(InMemoryAgentRepository())
    agent_runtime = AgentRuntime(agents, model_registry=models)
    responder = ModelRuntimeConversationResponseProvider(
        ModelRuntime(models),
        agent_runtime=agent_runtime,
    )
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    conversations = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    control_plane = ControlPlane(
        kernel=kernel,
        events=events,
        model_registry=models,
        conversation_service=conversations,
        conversation_agent_service=agents,
        conversation_response_provider=responder,
    )
    return (
        provider,
        models,
        agents,
        conversations,
        control_plane,
        ControlPlaneHTTP(control_plane),
        responder,
    )


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


def _context(key: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        idempotency_key=key,
        actor=ACTOR,
    )


def test_agent_conversation_pins_revision_and_uses_canonical_model_runtime(tmp_path: Path) -> None:
    provider, _, agents, conversations, control_plane, http, responder = _model_stack(tmp_path)
    owner = OwnerRef(type="user", id=ACTOR.owner_id)
    first = agents.create_agent(
        _profile("Always answer as revision one."),
        owner_ref=owner,
    )

    conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Pinned Agent chat",
            "target": {"kind": "agent", "id": first.agent_id},
        },
        key="create-agent-chat",
    )
    target = cast(
        dict[str, JsonValue], cast(dict[str, JsonValue], conversation["metadata"])["target"]
    )
    assert target == {"kind": "agent", "id": first.agent_id, "revision": 1}
    assert cast(dict[str, JsonValue], conversation["default_agent"])["revision"] == 1

    agents.update_agent(
        first.agent_id,
        _profile("This is revision two and must not affect the existing chat."),
        expected_revision=1,
    )
    message = _post(
        http,
        f"/api/v1/conversations/{conversation['id']}/messages",
        {"content": [{"kind": "text", "text": "Which pinned revision is answering?"}]},
        key="agent-chat-message",
    )

    stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            responder,
            _context("agent-runtime-response"),
            cast(str, message["id"]),
        )
    )
    events = _run(_collect(stream))

    assert [event["type"] for event in events] == [
        "conversation.response.delta",
        "conversation.response.committed",
    ]
    committed = cast(dict[str, JsonValue], events[-1]["message"])
    assert committed["model_config_id"] == MODEL_ID
    assert committed["sender_ref"] == f"agent:{first.agent_id}"
    assert "Canonical assistant answer" in json.dumps(committed)

    assert len(provider.calls) == 1
    model_request = provider.calls[0]
    assert model_request.context.correlation_id == "correlation-agent-runtime-response"
    assert model_request.context.owner_id == ACTOR.owner_id
    assert model_request.requirements["model_config_id"] == MODEL_ID
    serialized_request = json.dumps(
        {
            "messages": model_request.messages,
            "requirements": model_request.requirements,
        }
    )
    assert "Always answer as revision one." in serialized_request
    assert "revision two" not in serialized_request
    assert "provider-private/native-chat-model" not in json.dumps(events)


def test_team_conversation_pins_team_revision_and_uses_leader_model_policy(tmp_path: Path) -> None:
    provider, _, agents, conversations, control_plane, http, responder = _model_stack(
        tmp_path,
        response_text="Team response",
    )
    owner = OwnerRef(type="user", id=ACTOR.owner_id)
    leader = agents.create_agent(
        _profile("Lead the team conversation."),
        owner_ref=owner,
    )
    member = agents.create_agent(
        _profile("Secondary team member."),
        owner_ref=owner,
    )
    team = agents.create_team(
        AgentTeamProfile(
            name="Pinned Team",
            members=(
                AgentTeamMember(AgentRevisionRef(leader.agent_id, leader.revision), role="lead"),
                AgentTeamMember(AgentRevisionRef(member.agent_id, member.revision), role="member"),
            ),
            leader_agent_id=leader.agent_id,
        ),
        owner_ref=owner,
    )

    conversation = _post(
        http,
        "/api/v1/conversations",
        {
            "title": "Pinned Team chat",
            "target": {"kind": "agent_team", "id": team.team_id},
        },
        key="create-team-chat",
    )
    target = cast(
        dict[str, JsonValue], cast(dict[str, JsonValue], conversation["metadata"])["target"]
    )
    assert target == {"kind": "agent_team", "id": team.team_id, "revision": 1}

    message = _post(
        http,
        f"/api/v1/conversations/{conversation['id']}/messages",
        {"content": [{"kind": "text", "text": "Answer as the team."}]},
        key="team-chat-message",
    )
    stream = _run(
        stream_conversation_response(
            control_plane,
            conversations,
            responder,
            _context("team-runtime-response"),
            cast(str, message["id"]),
        )
    )
    _run(_collect(stream))

    assert len(provider.calls) == 1
    serialized = json.dumps(provider.calls[0].requirements)
    assert "Lead the team conversation." in serialized
    assert "Pinned Team" in serialized
    assert provider.calls[0].requirements["agent_id"] == leader.agent_id


def test_project_task_and_orchestrator_conversations_use_same_model_runtime_surface(
    tmp_path: Path,
) -> None:
    provider, _, _, conversations, control_plane, http, responder = _model_stack(tmp_path)
    project = _post(http, "/api/v1/projects", {"name": "Conversation Project"}, key="project")
    task = _post(
        http,
        "/api/v1/tasks",
        {
            "title": "Conversation Task",
            "objective": "Provide task-scoped conversational context",
            "project_id": project["id"],
        },
        key="task",
    )

    for index, target in enumerate(
        (
            {"kind": "project", "id": project["id"]},
            {"kind": "task", "id": task["id"]},
            {"kind": "orchestrator", "id": "platform"},
        )
    ):
        conversation = _post(
            http,
            "/api/v1/conversations",
            {"title": f"Runtime target {index}", "target": target},
            key=f"target-{index}",
        )
        message = _post(
            http,
            f"/api/v1/conversations/{conversation['id']}/messages",
            {"content": [{"kind": "text", "text": f"Turn {index}"}]},
            key=f"target-message-{index}",
        )
        stream = _run(
            stream_conversation_response(
                control_plane,
                conversations,
                responder,
                _context(f"target-response-{index}"),
                cast(str, message["id"]),
            )
        )
        events = _run(_collect(stream))
        assert events[-1]["type"] == "conversation.response.committed"

    assert len(provider.calls) == 3
    assert all(call.requirements["model_config_id"] == MODEL_ID for call in provider.calls)
