from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.agents import AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.agents.models import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    InstructionSource,
    ModelFallbackPolicy,
)
from ai_multi_agent_platform.contracts import (
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    AgentSelectionRef,
    ConversationContentBlock,
    ConversationMessage,
    ConversationResponseChunk,
    ConversationResponseRequest,
    ConversationResponseTarget,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
    ModelRuntimeConversationResponseProvider,
    ParticipantKind,
    ReferenceKind,
    ResourceReference,
    resolve_conversation_context,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    ModelRuntime,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider


async def _collect(
    stream: AsyncIterator[ConversationResponseChunk],
) -> tuple[ConversationResponseChunk, ...]:
    return tuple([item async for item in stream])


def _message(
    *,
    conversation_id: str | None = None,
    text: str = "Use the attached canonical context.",
    references: tuple[ResourceReference, ...] = (),
) -> ConversationMessage:
    return ConversationMessage(
        conversation_id=conversation_id or new_id("conversation"),
        sender_ref="user:alice",
        role=MessageRole.USER,
        content=(ConversationContentBlock.text_block(text),),
        references=references,
    )


def _response_request(
    message: ConversationMessage,
    *,
    target: ConversationResponseTarget | None = None,
    project_id: str | None = None,
) -> ConversationResponseRequest:
    return ConversationResponseRequest(
        request_id="request-issue-72-audit",
        correlation_id="correlation-issue-72-audit",
        actor_ref="user:alice",
        conversation_id=message.conversation_id,
        source_message_id=message.id,
        target=target or ConversationResponseTarget(kind="orchestrator", id="platform"),
        history=(message,),
        project_id=project_id,
    )


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-72-context-setup",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def _registry() -> tuple[ModelRegistry, FakeModelProvider]:
    provider = FakeModelProvider(response_text="Canonical answer")
    registry = ModelRegistry()
    registry.register_provider(provider)
    return registry, provider


def _register_model(
    registry: ModelRegistry,
    provider: FakeModelProvider,
    config_id: str,
    *,
    context_window: int,
    priority: int = 0,
) -> None:
    registry.register_model(
        ModelConfiguration(
            config_id=config_id,
            display_name=config_id,
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=context_window, modalities=("text",)),
            health=HealthStatus.HEALTHY,
            priority=priority,
        )
    )


def _agent_profile(model: AgentModelPolicy) -> AgentProfile:
    return AgentProfile(
        name="Conversation Agent",
        role="assistant",
        instructions=AgentInstructions(
            role=InstructionSource(content="Answer through canonical policy.", version="1")
        ),
        model=model,
    )


def test_agent_conversation_task_handoff_inherits_exact_assignment(tmp_path: Path) -> None:
    service = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    agent_id = new_id("agent")
    conversation = asyncio.run(
        service.create_conversation(
            title="Agent task handoff",
            owner_ref="user:alice",
            default_agent=AgentSelectionRef(
                kind=ParticipantKind.AGENT,
                id=agent_id,
                revision=4,
            ),
        )
    )
    message = asyncio.run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Make this durable."),),
        )
    )
    captured: dict[str, JsonValue] = {}

    async def create_task(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        captured.update(payload)
        return {"id": new_id("task")}

    asyncio.run(
        service.handoff_message_to_task(
            message_id=message.id,
            create_task=create_task,
            task_payload={"title": "Durable", "objective": "Use canonical AgentRuntime"},
        )
    )

    assert captured["agent_assignment"] == {
        "kind": "agent",
        "id": agent_id,
        "revision": 4,
        "required": True,
        "policy_ref": None,
    }


def test_team_conversation_task_handoff_inherits_exact_assignment(tmp_path: Path) -> None:
    service = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    team_id = new_id("team")
    conversation = asyncio.run(
        service.create_conversation(
            title="Team task handoff",
            owner_ref="user:alice",
            default_agent=AgentSelectionRef(
                kind=ParticipantKind.AGENT_TEAM,
                id=team_id,
                revision=3,
            ),
        )
    )
    message = asyncio.run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Make this durable."),),
        )
    )
    captured: dict[str, JsonValue] = {}

    async def create_task(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        captured.update(payload)
        return {"id": new_id("task")}

    asyncio.run(
        service.handoff_message_to_task(
            message_id=message.id,
            create_task=create_task,
            task_payload={"title": "Durable", "objective": "Use canonical Team runtime"},
        )
    )

    assert captured["agent_assignment"] == {
        "kind": "agent_team",
        "id": team_id,
        "revision": 3,
        "required": True,
        "policy_ref": None,
    }


def test_file_reference_is_resolved_into_ephemeral_model_context(tmp_path: Path) -> None:
    project_id = new_id("project")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    record = asyncio.run(
        files.create_file(
            b"deployment target is blue",
            _data_context(project_id),
            content_type="text/plain",
        )
    )
    reference = ResourceReference(kind=ReferenceKind.FILE, id=record.file_id)
    message = _message(references=(reference,))
    request = _response_request(message, project_id=project_id)

    resolved = asyncio.run(
        resolve_conversation_context(
            request,
            file_provider=files,
            knowledge_provider=None,
        )
    )

    assert len(resolved) == 1
    assert resolved[0].kind == "file"
    assert resolved[0].id == record.file_id
    assert "deployment target is blue" in resolved[0].text
    assert "deployment target is blue" not in str(message.to_json())
    assert record.file_id in str(message.to_json())


def test_binary_file_reference_is_metadata_only_even_when_bytes_are_utf8(tmp_path: Path) -> None:
    project_id = new_id("project")
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    sensitive_binary_text = b"binary-marked-secret-that-happens-to-be-valid-utf8"
    record = asyncio.run(
        files.create_file(
            sensitive_binary_text,
            _data_context(project_id),
            content_type="application/octet-stream",
        )
    )
    message = _message(references=(ResourceReference(kind=ReferenceKind.FILE, id=record.file_id),))

    resolved = asyncio.run(
        resolve_conversation_context(
            _response_request(message, project_id=project_id),
            file_provider=files,
            knowledge_provider=None,
        )
    )

    assert len(resolved) == 1
    assert "binary content is not injected as text" in resolved[0].text
    assert "binary-marked-secret-that-happens-to-be-valid-utf8" not in resolved[0].text
    assert "application/octet-stream" in resolved[0].text


def test_knowledge_reference_is_resolved_into_ephemeral_model_context(tmp_path: Path) -> None:
    project_id = new_id("project")
    knowledge = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        title="Deployment notes",
        revision="v1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    context = _data_context(project_id)
    asyncio.run(knowledge.register_source(source, context))
    asyncio.run(
        knowledge.ingest_source(
            source.source_id,
            "deployment target blue remains canonical",
            "notes/deployment.md",
            context,
        )
    )
    message = _message(
        text="Which deployment target should I use?",
        references=(ResourceReference(kind=ReferenceKind.KNOWLEDGE, id=source.source_id),),
    )
    request = _response_request(message, project_id=project_id)

    resolved = asyncio.run(
        resolve_conversation_context(
            request,
            file_provider=None,
            knowledge_provider=knowledge,
        )
    )

    assert len(resolved) == 1
    assert resolved[0].kind == "knowledge"
    assert resolved[0].id == source.source_id
    assert "deployment target blue remains canonical" in resolved[0].text
    assert "deployment target blue remains canonical" not in str(message.to_json())


def test_agent_conversation_applies_routing_profile_and_fallback() -> None:
    registry, provider = _registry()
    _register_model(registry, provider, "model-small", context_window=4_096, priority=100)
    _register_model(registry, provider, "model-large", context_window=32_768, priority=1)
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile(
            AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id="model-small"),
                routing_profile_ref="large-context",
                allow_task_override=True,
                fallback=ModelFallbackPolicy.ROUTE,
            )
        ),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    message = _message()
    request = _response_request(
        message,
        target=ConversationResponseTarget(kind="agent", id=agent.agent_id, revision=agent.revision),
    )
    responder = ModelRuntimeConversationResponseProvider(
        ModelRuntime(registry),
        agents,
        routing_profiles={"large-context": RoutingRequirements(min_context_window=16_000)},
    )

    chunks = asyncio.run(_collect(responder.stream_response(request)))

    assert chunks[-1].model_config_id == "model-large"
    assert provider.calls[-1].requirements["model_config_id"] == "model-large"
    assert provider.calls[-1].requirements["min_context_window"] == 16_000
