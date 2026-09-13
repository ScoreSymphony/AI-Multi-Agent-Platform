from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar, cast

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.data import (
    DataAccessContext,
    IndexReference,
    KnowledgeProvider,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
    new_knowledge_index_id,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

ACTOR = ActorContext(
    principal_ref="user:issue-72-knowledge",
    owner_type="user",
    owner_id="issue-72-knowledge",
    actor_type="human",
)


def _run(value: Any) -> Any:
    return asyncio.run(value)


def _context(project_id: str, *, key: str = "knowledge-message") -> RequestContext:
    return RequestContext(
        request_id=f"request-{key}",
        correlation_id=f"correlation-{key}",
        actor=ACTOR,
        idempotency_key=key,
    )


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="knowledge-setup",
            owner_type=ACTOR.owner_type,
            owner_id=ACTOR.owner_id,
            project_id=project_id,
        ),
        actor_ref=ACTOR.principal_ref,
    )


def _source(project_id: str) -> KnowledgeSource:
    now = datetime.now(UTC)
    return KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref=ACTOR.principal_ref,
        created_by=ACTOR.principal_ref,
        title="Conversation knowledge source",
        revision="v1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )


def _stack(
    tmp_path: Path,
    *,
    knowledge: KnowledgeProvider | None,
) -> tuple[ConversationService, ControlPlane]:
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
        conversation_service=conversations,
        conversation_knowledge_provider=knowledge,
    )
    return conversations, control_plane


def _message_payload(source_id: str) -> dict[str, Any]:
    return {
        "content": [{"kind": "text", "text": "Use the attached knowledge source."}],
        "references": [
            {
                "kind": "knowledge",
                "id": source_id,
                "metadata": {},
            }
        ],
    }


def test_message_persists_only_canonical_knowledge_source_reference(tmp_path: Path) -> None:
    project_id = new_id("project")
    provider = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    source = _source(project_id)
    _run(provider.register_source(source, _data_context(project_id)))
    conversations, control_plane = _stack(tmp_path, knowledge=provider)
    conversation = _run(
        conversations.create_conversation(
            title="Knowledge conversation",
            owner_ref=ACTOR.principal_ref,
            project_id=project_id,
        )
    )

    message = _run(
        control_plane.execute_command(
            _context(project_id),
            "conversation.message.add",
            conversation.id,
            _message_payload(source.source_id),
        )
    )

    assert message["references"] == [
        {
            "kind": "knowledge",
            "id": source.source_id,
            "label": None,
            "metadata": {},
        }
    ]
    serialized = str(message)
    assert "knowledge_index_" not in serialized
    assert "knowledge_document_" not in serialized


def test_message_rejects_cross_project_knowledge_source(tmp_path: Path) -> None:
    source_project = new_id("project")
    conversation_project = new_id("project")
    provider = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    source = _source(source_project)
    _run(provider.register_source(source, _data_context(source_project)))
    conversations, control_plane = _stack(tmp_path, knowledge=provider)
    conversation = _run(
        conversations.create_conversation(
            title="Cross-project knowledge",
            owner_ref=ACTOR.principal_ref,
            project_id=conversation_project,
        )
    )

    with pytest.raises(ContractError) as error:
        _run(
            control_plane.execute_command(
                _context(conversation_project, key="cross-project"),
                "conversation.message.add",
                conversation.id,
                _message_payload(source.source_id),
            )
        )
    assert error.value.code is ErrorCode.FORBIDDEN


def test_message_rejects_knowledge_reference_without_provider(tmp_path: Path) -> None:
    project_id = new_id("project")
    conversations, control_plane = _stack(tmp_path, knowledge=None)
    conversation = _run(
        conversations.create_conversation(
            title="Missing knowledge provider",
            owner_ref=ACTOR.principal_ref,
            project_id=project_id,
        )
    )

    with pytest.raises(ContractError) as error:
        _run(
            control_plane.execute_command(
                _context(project_id, key="missing-provider"),
                "conversation.message.add",
                conversation.id,
                _message_payload(new_knowledge_source_id()),
            )
        )
    assert error.value.code is ErrorCode.UNAVAILABLE


def test_knowledge_validation_uses_replaceable_public_provider_contract(tmp_path: Path) -> None:
    project_id = new_id("project")
    source_id = new_knowledge_source_id()

    class ReplacementKnowledgeProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, DataAccessContext]] = []

        async def get_index_status(
            self,
            requested_source_id: str,
            context: DataAccessContext,
        ) -> IndexReference:
            self.calls.append((requested_source_id, context))
            return IndexReference(
                index_id=new_knowledge_index_id(),
                source_id=requested_source_id,
                revision="replacement-v1",
                status=KnowledgeStatus.READY,
                updated_at=datetime.now(UTC),
            )

    replacement = ReplacementKnowledgeProvider()
    conversations, control_plane = _stack(
        tmp_path,
        knowledge=cast(KnowledgeProvider, replacement),
    )
    conversation = _run(
        conversations.create_conversation(
            title="Replaceable knowledge provider",
            owner_ref=ACTOR.principal_ref,
            project_id=project_id,
        )
    )

    _run(
        control_plane.execute_command(
            _context(project_id, key="replacement-provider"),
            "conversation.message.add",
            conversation.id,
            _message_payload(source_id),
        )
    )

    assert len(replacement.calls) == 1
    called_source, called_context = replacement.calls[0]
    assert called_source == source_id
    assert called_context.operation.project_id == project_id
    assert called_context.actor_ref == ACTOR.principal_ref


def test_knowledge_reference_requires_canonical_source_id() -> None:
    from ai_multi_agent_platform.conversations import ReferenceKind, ResourceReference

    with pytest.raises(ValueError):
        ResourceReference(
            kind=ReferenceKind.KNOWLEDGE,
            id=new_id("file"),
        )
