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
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
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
        visible_project_id = new_id("project")
        visible_workspace_id = new_id("workspace")
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
            project_id=visible_project_id,
            workspace_id=visible_workspace_id,
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
        private_other_message = await conversations.append_message(
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
        denied_project_message = await conversations.append_message(
            conversation_id=denied_project.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("denied-project-secret-needle"),),
        )

        exact = await _search(http, type="conversation", id=visible.id)
        assert exact["total"] == 1
        assert _items(exact)[0]["canonical_ref"] == f"/api/v1/conversations/{visible.id}"

        exact_message = await _search(http, type="conversation-message", id=visible_message.id)
        assert exact_message["total"] == 1
        assert _items(exact_message)[0]["resource_id"] == visible_message.id
        assert _items(exact_message)[0]["canonical_ref"] == (
            f"/api/v1/conversation-messages/{visible_message.id}"
        )

        keyword = await _search(http, type="conversation-message", q="global-search-visible-needle")
        assert keyword["total"] == 1
        assert _items(keyword)[0]["resource_id"] == visible_message.id
        assert _items(keyword)[0]["summary"] == "global-search-visible-needle"

        project_scoped = await _search(
            http,
            type="conversation",
            project_id=visible_project_id,
        )
        assert project_scoped["total"] == 1
        assert _items(project_scoped)[0]["resource_id"] == visible.id

        workspace_scoped = await _search(
            http,
            type="conversation-message",
            workspace_id=visible_workspace_id,
        )
        assert workspace_scoped["total"] == 1
        assert _items(workspace_scoped)[0]["resource_id"] == visible_message.id

        visible_message_count = await _search(http, type="conversation-message")
        assert visible_message_count["total"] == 1
        assert _items(visible_message_count)[0]["resource_id"] == visible_message.id

        private = await _search(http, q="private-bob-secret-needle")
        assert private["total"] == 0
        private_exact = await _search(http, type="conversation", id=private_other.id)
        assert private_exact["total"] == 0
        private_message_exact = await _search(
            http,
            type="conversation-message",
            id=private_other_message.id,
        )
        assert private_message_exact["total"] == 0

        project_hidden = await _search(http, q="denied-project-secret-needle")
        assert project_hidden["total"] == 0
        project_exact = await _search(http, type="conversation", id=denied_project.id)
        assert project_exact["total"] == 0
        project_message_exact = await _search(
            http,
            type="conversation-message",
            id=denied_project_message.id,
        )
        assert project_message_exact["total"] == 0

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


def test_single_node_control_plane_composition_exposes_conversation_search(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "test-only-admin-password-12345")
        assert deployment.control_plane.conversation_service is deployment.conversations

        conversation = await deployment.conversations.create_conversation(
            title="Single-node searchable conversation",
            owner_ref=admin.user_id,
        )
        message = await deployment.conversations.append_message(
            conversation_id=conversation.id,
            sender_ref=admin.user_id,
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("single-node-search-needle"),),
        )

        http = ControlPlaneHTTP(deployment.control_plane)
        headers = {
            "Content-Type": "application/json",
            "X-Principal-Ref": admin.user_id,
            "X-Owner-Type": "user",
            "X-Owner-Id": admin.user_id,
        }

        exact = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                headers=headers,
                query={"type": "conversation", "id": conversation.id},
            )
        )
        assert exact.status == 200
        assert isinstance(exact.body, dict)
        assert exact.body["total"] == 1
        exact_items = _items(exact.body)
        assert exact_items[0]["resource_id"] == conversation.id
        assert exact_items[0]["canonical_ref"] == f"/api/v1/conversations/{conversation.id}"

        keyword = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/search",
                headers=headers,
                query={"type": "conversation-message", "q": "single-node-search-needle"},
            )
        )
        assert keyword.status == 200
        assert isinstance(keyword.body, dict)
        assert keyword.body["total"] == 1
        keyword_items = _items(keyword.body)
        assert keyword_items[0]["resource_id"] == message.id
        assert keyword_items[0]["canonical_ref"] == (
            f"/api/v1/conversation-messages/{message.id}"
        )
        assert keyword_items[0]["summary"] == "single-node-search-needle"

    asyncio.run(scenario())
