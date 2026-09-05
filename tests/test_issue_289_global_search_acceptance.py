from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationRetentionManager,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _ConversationSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, denied_project_id: str | None = None) -> None:
        super().__init__()
        self.denied_project_id = denied_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.context.project_id == self.denied_project_id:
            return AuthorizationDecision(allowed=False, reason="project-hidden")
        return AuthorizationDecision(allowed=True, reason="visible")


def _headers(principal: str = "user:alice") -> dict[str, str]:
    _, owner_id = principal.split(":", 1)
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": principal,
        "X-Owner-Type": "user",
        "X-Owner-Id": owner_id,
    }


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_global_search_discovers_allowed_chat_and_hides_private_and_denied_project_chat(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        denied_project_id = new_id("project")
        authorization = _ConversationSearchAuthorization(denied_project_id)
        conversations = ConversationService(
            JsonConversationRepository(tmp_path / "conversations.json")
        )
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        control_plane = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=authorization,
            conversation_service=conversations,
        )
        http = ControlPlaneHTTP(control_plane)

        visible = await conversations.create_conversation(
            title="Visible canonical conversation",
            owner_ref="user:alice",
            metadata={"provider_session_id": "provider-native-hidden"},
        )
        visible_message = await conversations.append_message(
            conversation_id=visible.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("global-search-visible-needle"),),
            metadata={"provider_session_id": "message-native-hidden"},
        )
        private_other = await conversations.create_conversation(
            title="Private Bob conversation",
            owner_ref="user:bob",
        )
        await conversations.append_message(
            conversation_id=private_other.id,
            sender_ref="user:bob",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("private-bob-secret-needle"),),
        )
        denied_project = await conversations.create_conversation(
            title="Denied project conversation",
            owner_ref="user:alice",
            project_id=denied_project_id,
        )
        await conversations.append_message(
            conversation_id=denied_project.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("denied-project-secret-needle"),),
        )

        exact = await _search(http, type="conversation", id=visible.id)
        assert exact["total"] == 1
        assert _items(exact)[0]["canonical_ref"] == f"/api/v1/conversations/{visible.id}"

        keyword = await _search(http, type="conversation-message", q="global-search-visible-needle")
        assert keyword["total"] == 1
        assert _items(keyword)[0]["resource_id"] == visible_message.id
        assert _items(keyword)[0]["summary"] == "global-search-visible-needle"

        private = await _search(http, q="private-bob-secret-needle")
        assert private["total"] == 0
        private_exact = await _search(http, type="conversation", id=private_other.id)
        assert private_exact["total"] == 0

        project_hidden = await _search(http, q="denied-project-secret-needle")
        assert project_hidden["total"] == 0
        project_exact = await _search(http, type="conversation", id=denied_project.id)
        assert project_exact["total"] == 0

        provider_native = await _search(http, q="provider-native-hidden")
        assert provider_native["total"] == 0
        message_native = await _search(http, q="message-native-hidden")
        assert message_native["total"] == 0

        await ConversationRetentionManager(conversations).tombstone(visible.id)
        after_delete = await _search(http, type="conversation", id=visible.id)
        assert after_delete["total"] == 0
        deleted_message = await _search(
            http,
            type="conversation-message",
            id=visible_message.id,
        )
        assert deleted_message["total"] == 0
        deleted_text = await _search(http, q="global-search-visible-needle")
        assert deleted_text["total"] == 0

    asyncio.run(scenario())
