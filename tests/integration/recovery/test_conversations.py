from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    AgentSelectionRef,
    ConversationContentBlock,
    ConversationParticipant,
    ConversationService,
    ConversationStatus,
    JsonConversationRepository,
    MessageRole,
    ModelRoutingPreference,
    ParticipantKind,
    ReferenceKind,
)
from ai_multi_agent_platform.domain import new_id


def _service(path: Path) -> ConversationService:
    return ConversationService(JsonConversationRepository(path))


def test_conversations_create_list_archive_reopen_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = tmp_path / "conversations.json"
        service = _service(store)
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        agent_id = new_id("agent")
        conversation = await service.create_conversation(
            title="Plan a release",
            owner_ref="user:alice",
            project_id=project_id,
            workspace_id=workspace_id,
            participants=(ConversationParticipant(ParticipantKind.USER, "user:alice"),),
            default_agent=AgentSelectionRef(ParticipantKind.AGENT, agent_id, revision=2),
            model_preference=ModelRoutingPreference(
                model_config_id="balanced-general",
                routing_requirements={"capability": "reasoning"},
            ),
        )

        assert await service.list_conversations(owner_ref="user:alice") == (conversation,)
        archived = await service.archive_conversation(conversation.id)
        assert archived.status is ConversationStatus.ARCHIVED
        assert await service.list_conversations(owner_ref="user:alice") == ()
        assert await service.list_conversations(owner_ref="user:alice", include_archived=True) == (
            archived,
        )

        restarted = _service(store)
        recovered = await restarted.get_conversation(conversation.id)
        assert recovered == archived
        reopened = await restarted.reopen_conversation(conversation.id)
        assert reopened.status is ConversationStatus.OPEN
        assert reopened.default_agent == conversation.default_agent
        assert reopened.model_preference == conversation.model_preference

    asyncio.run(scenario())


def test_message_history_is_durable_and_paginated(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = tmp_path / "conversations.json"
        service = _service(store)
        conversation = await service.create_conversation(
            title="Research",
            owner_ref="user:alice",
        )
        first = await service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("First"),),
        )
        second = await service.append_message(
            conversation_id=conversation.id,
            sender_ref="agent:assistant",
            role=MessageRole.ASSISTANT,
            content=(ConversationContentBlock.text_block("Second"),),
            model_config_id="balanced-general",
            model_provider_ref="provider:local",
        )

        page, cursor = await service.list_messages(conversation.id, limit=1)
        assert page == (first,)
        assert cursor == "1"
        next_page, next_cursor = await service.list_messages(
            conversation.id, limit=1, cursor=cursor
        )
        assert next_page == (second,)
        assert next_cursor is None

        restarted = _service(store)
        recovered, _ = await restarted.list_messages(conversation.id)
        assert recovered == (first, second)

    asyncio.run(scenario())


def test_message_to_task_handoff_uses_canonical_task_creator_and_links_result(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        service = _service(tmp_path / "conversations.json")
        conversation = await service.create_conversation(
            title="Implement feature",
            owner_ref="user:alice",
        )
        message = await service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Implement the parser"),),
        )
        created_payloads: list[dict[str, JsonValue]] = []
        task_id = new_id("task")

        async def create_task(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
            created_payloads.append(payload)
            return {"id": task_id, "status": "draft"}

        linked_message, linked_conversation, task = await service.handoff_message_to_task(
            message_id=message.id,
            create_task=create_task,
            task_payload={"title": "Implement parser", "metadata": {"source": "chat"}},
        )

        assert task["id"] == task_id
        assert linked_conversation.task_ids == (task_id,)
        assert any(
            ref.kind is ReferenceKind.TASK and ref.id == task_id
            for ref in linked_message.references
        )
        metadata = created_payloads[0]["metadata"]
        assert isinstance(metadata, dict)
        assert metadata == {
            "source": "chat",
            "conversation_id": conversation.id,
            "conversation_message_id": message.id,
        }

    asyncio.run(scenario())


def test_run_and_artifact_links_remain_references_not_chat_lifecycle_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path / "conversations.json")
        conversation = await service.create_conversation(title="Run", owner_ref="user:alice")
        message = await service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Run it"),),
        )
        run_id = new_id("run")
        artifact_id = new_id("artifact")

        await service.link_run(
            conversation_id=conversation.id,
            run_id=run_id,
            message_id=message.id,
        )
        linked = await service.link_artifact(
            conversation_id=conversation.id,
            artifact_id=artifact_id,
            message_id=message.id,
        )
        messages, _ = await service.list_messages(conversation.id)

        assert linked.run_ids == (run_id,)
        assert linked.artifact_ids == (artifact_id,)
        assert {(ref.kind, ref.id) for ref in messages[0].references} == {
            (ReferenceKind.RUN, run_id),
            (ReferenceKind.ARTIFACT, artifact_id),
        }

    asyncio.run(scenario())


def test_archived_conversation_is_readable_but_rejects_new_messages(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _service(tmp_path / "conversations.json")
        conversation = await service.create_conversation(title="Archive", owner_ref="user:alice")
        await service.archive_conversation(conversation.id)
        try:
            await service.append_message(
                conversation_id=conversation.id,
                sender_ref="user:alice",
                role=MessageRole.USER,
                content=(ConversationContentBlock.text_block("blocked"),),
            )
        except ValueError as exc:
            assert "open conversations" in str(exc)
        else:
            raise AssertionError("archived conversation accepted a new message")

    asyncio.run(scenario())
