from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.conversations import ConversationContentBlock, MessageRole
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

PASSWORD = "correct horse battery staple"


def test_single_node_exposes_and_persists_canonical_conversations_across_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        first = build_single_node_deployment(config)
        admin = first.bootstrap_admin("admin", PASSWORD)
        credential = first.authentication.create_personal_access_token(
            admin.user_id,
            purpose="conversation-manifest-test",
        )

        assert first.control_plane.conversation_service is first.conversations
        assert first.control_plane.conversation_response_provider is None

        manifest = await first.http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1",
                headers={"authorization": f"Bearer {credential.secret}"},
            )
        )
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        resources = manifest.body["resources"]
        assert isinstance(resources, list)
        assert "conversations" in resources
        assert "conversation-messages" in resources
        assert "conversation-exports" in resources

        conversation = await first.conversations.create_conversation(
            title="Persisted single-node conversation",
            owner_ref=admin.user_id,
        )
        message = await first.conversations.append_message(
            conversation_id=conversation.id,
            sender_ref=admin.user_id,
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("Persist this message across restart."),),
        )

        restarted = build_single_node_deployment(config)
        assert restarted.control_plane.conversation_service is restarted.conversations
        persisted = await restarted.conversations.get_conversation(conversation.id)
        history, cursor = await restarted.conversations.list_messages(conversation.id, limit=20)

        assert persisted.id == conversation.id
        assert persisted.title == conversation.title
        assert cursor is None
        assert [item.id for item in history] == [message.id]
        assert history[0].content[0].text == "Persist this message across restart."

    asyncio.run(scenario())
