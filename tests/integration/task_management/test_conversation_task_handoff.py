from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    AgentSelectionRef,
    ConversationContentBlock,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
    ParticipantKind,
)
from ai_multi_agent_platform.domain import new_id


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
