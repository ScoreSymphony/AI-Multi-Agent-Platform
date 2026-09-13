from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane.conversation_api import (
    CONVERSATION_COLLECTION,
    CONVERSATION_MESSAGE_COLLECTION,
)
from ai_multi_agent_platform.control_plane.conversation_search import (
    conversation_search_result_allowed,
    install_conversation_search_services,
)
from ai_multi_agent_platform.control_plane.extensions import ResourceService
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.conversations import (
    ContentKind,
    ConversationContentBlock,
    ConversationRetentionManager,
    ConversationRetentionMode,
    ConversationRetentionPolicy,
    ConversationService,
    JsonConversationRepository,
    MessageRole,
    MessageStatus,
)
from ai_multi_agent_platform.search import (
    LocalSearchProvider,
    SearchMode,
    SearchQuery,
    document_from_resource,
)


def _run(coro):
    return asyncio.run(coro)


class _Delegate(ResourceService):
    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, object], ...]:
        del context, query
        return ()

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, object]:
        del context
        return {"id": resource_id, "type": "delegate"}


class _AllowingControlPlane:
    def __init__(self) -> None:
        self._resource_services: dict[str, ResourceService] = {
            CONVERSATION_COLLECTION: _Delegate(),
            CONVERSATION_MESSAGE_COLLECTION: _Delegate(),
        }

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool:
        del (
            context,
            action,
            resource_ref,
            owner_type,
            owner_id,
            project_id,
            request_payload_digest,
        )
        return True


def _service(tmp_path: Path) -> ConversationService:
    return ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))


def _context(principal_ref: str) -> RequestContext:
    return RequestContext(
        request_id="request_search",
        correlation_id="correlation_search",
        actor=ActorContext(principal_ref=principal_ref),
    )


async def _search_resources(
    control_plane: _AllowingControlPlane,
) -> tuple[tuple[dict[str, object], ...], tuple[dict[str, object], ...]]:
    conversations = control_plane._resource_services[CONVERSATION_COLLECTION]
    messages = control_plane._resource_services[CONVERSATION_MESSAGE_COLLECTION]
    conversation_resources = await conversations.list_search_resources()  # type: ignore[attr-defined]
    message_resources = await messages.list_search_resources()  # type: ignore[attr-defined]
    return conversation_resources, message_resources


def test_exact_conversation_and_permitted_message_keyword_search(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conversation = _run(
        service.create_conversation(
            title="Search integration thread",
            summary="Canonical chat discovery",
            owner_ref="user:alice",
        )
    )
    message = _run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("needle permitted message text"),),
        )
    )
    control_plane = _AllowingControlPlane()
    install_conversation_search_services(control_plane, service)
    conversation_resources, message_resources = _run(_search_resources(control_plane))

    documents = tuple(
        [
            document_from_resource(resource, collection=CONVERSATION_COLLECTION)
            for resource in conversation_resources
        ]
        + [
            document_from_resource(resource, collection=CONVERSATION_MESSAGE_COLLECTION)
            for resource in message_resources
        ]
    )
    provider = LocalSearchProvider()
    operation = OperationContext(correlation_id="search-test")
    _run(provider.rebuild(documents, operation))

    exact = _run(
        provider.search(
            SearchQuery(
                exact_id=conversation.id,
                resource_types=("conversation",),
                mode=SearchMode.EXACT,
            ),
            operation,
        )
    )
    assert exact.total == 1
    assert exact.items[0].resource_id == conversation.id
    assert exact.items[0].canonical_ref == f"/api/v1/conversations/{conversation.id}"

    keyword = _run(
        provider.search(
            SearchQuery(text="needle", resource_types=("conversation-message",)),
            operation,
        )
    )
    assert keyword.total == 1
    assert keyword.items[0].resource_id == message.id
    assert keyword.items[0].summary == "needle permitted message text"
    assert keyword.items[0].canonical_ref == f"/api/v1/conversation-messages/{message.id}"


def test_private_conversation_search_reauthorizes_against_canonical_owner(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conversation = _run(
        service.create_conversation(
            title="Alice private thread",
            owner_ref="user:alice",
        )
    )
    control_plane = _AllowingControlPlane()
    install_conversation_search_services(control_plane, service)
    conversation_resources, _ = _run(_search_resources(control_plane))
    result = document_from_resource(
        conversation_resources[0],
        collection=CONVERSATION_COLLECTION,
    )
    provider = LocalSearchProvider()
    operation = OperationContext(correlation_id="search-private")
    _run(provider.rebuild((result,), operation))
    page = _run(
        provider.search(
            SearchQuery(exact_id=conversation.id, mode=SearchMode.EXACT),
            operation,
        )
    )

    assert _run(
        conversation_search_result_allowed(
            control_plane,
            service,
            _context("user:alice"),
            page.items[0],
        )
    )
    assert not _run(
        conversation_search_result_allowed(
            control_plane,
            service,
            _context("user:bob"),
            page.items[0],
        )
    )


def test_search_projection_excludes_json_metadata_and_provider_session_ids(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conversation = _run(
        service.create_conversation(
            title="Safe projection",
            owner_ref="user:alice",
            metadata={"provider_session_id": "native-session-secret"},
        )
    )
    _run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(
                ConversationContentBlock.text_block("ordinary searchable text"),
                ConversationContentBlock(
                    kind=ContentKind.JSON,
                    value={"provider_session_id": "json-session-secret"},
                ),
            ),
            metadata={"provider_session_id": "message-session-secret"},
        )
    )
    control_plane = _AllowingControlPlane()
    install_conversation_search_services(control_plane, service)
    conversation_resources, message_resources = _run(_search_resources(control_plane))

    serialized = repr((conversation_resources, message_resources))
    assert "native-session-secret" not in serialized
    assert "json-session-secret" not in serialized
    assert "message-session-secret" not in serialized
    assert "ordinary searchable text" in serialized


def test_expired_retention_and_tombstones_are_removed_from_rebuild_source(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conversation = _run(
        service.create_conversation(
            title="Expiring thread",
            owner_ref="user:alice",
        )
    )
    message = _run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("retention needle"),),
        )
    )
    control_plane = _AllowingControlPlane()
    install_conversation_search_services(control_plane, service)
    before_conversations, before_messages = _run(_search_resources(control_plane))
    assert {item["id"] for item in before_conversations} == {conversation.id}
    assert {item["id"] for item in before_messages} == {message.id}

    retention = ConversationRetentionManager(service)
    _run(
        retention.set_policy(
            conversation.id,
            ConversationRetentionPolicy(
                mode=ConversationRetentionMode.UNTIL,
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            ),
        )
    )
    expired_conversations, expired_messages = _run(_search_resources(control_plane))
    assert expired_conversations == ()
    assert expired_messages == ()

    _run(retention.tombstone(conversation.id))
    deleted_conversations, deleted_messages = _run(_search_resources(control_plane))
    assert deleted_conversations == ()
    assert deleted_messages == ()


def test_individual_message_tombstone_is_not_indexed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    conversation = _run(
        service.create_conversation(title="Message deletion", owner_ref="user:alice")
    )
    message = _run(
        service.append_message(
            conversation_id=conversation.id,
            sender_ref="user:alice",
            role=MessageRole.USER,
            content=(ConversationContentBlock.text_block("deleted message needle"),),
        )
    )
    tombstoned = replace(
        message,
        status=MessageStatus.TOMBSTONED,
        content=(ConversationContentBlock(kind=ContentKind.JSON, value={"tombstoned": True}),),
        revision=message.revision + 1,
        edited_at=datetime.now(UTC),
    )
    _run(service._repository.save_message(tombstoned))

    control_plane = _AllowingControlPlane()
    install_conversation_search_services(control_plane, service)
    _, message_resources = _run(_search_resources(control_plane))
    assert message_resources == ()
