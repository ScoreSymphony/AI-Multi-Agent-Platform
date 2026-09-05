"""Privacy-safe global Search integration for canonical Conversations (#289).

Search remains derived discovery state. Conversation/Message lifecycle, retention and
access decisions stay owned by the canonical #72 domain and are re-checked before a
Search result can become caller-visible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    ContentKind,
    Conversation,
    ConversationMessage,
    ConversationRetentionManager,
    ConversationRetentionMode,
    ConversationService,
    ConversationStatus,
    MessageStatus,
)
from ai_multi_agent_platform.search import SearchResult

from .conversation_api import CONVERSATION_COLLECTION, CONVERSATION_MESSAGE_COLLECTION
from .extensions import ResourceService
from .models import PageQuery, RequestContext


class _ConversationSearchControlPlane(Protocol):
    _resource_services: dict[str, ResourceService]

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
    ) -> bool: ...


class _SearchableConversationResourceService:
    """Delegate normal API reads while exposing a privacy-safe Search rebuild view."""

    def __init__(self, delegate: ResourceService, service: ConversationService) -> None:
        self._delegate = delegate
        self._service = service
        self._retention = ConversationRetentionManager(service)

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        return await self._delegate.list_resources(context, query)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return await self._delegate.get_resource(context, resource_id)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        conversations = await self._service.list_conversations(include_archived=True)
        now = datetime.now(UTC)
        return tuple(
            _conversation_search_resource(conversation)
            for conversation in conversations
            if _conversation_indexable(conversation, self._retention, now=now)
        )


class _SearchableConversationMessageResourceService:
    """Expose only retained, non-redacted Message text to the derived Search index."""

    def __init__(self, delegate: ResourceService, service: ConversationService) -> None:
        self._delegate = delegate
        self._service = service
        self._retention = ConversationRetentionManager(service)

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        return await self._delegate.list_resources(context, query)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return await self._delegate.get_resource(context, resource_id)

    async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]:
        resources: list[dict[str, JsonValue]] = []
        now = datetime.now(UTC)
        for conversation in await self._service.list_conversations(include_archived=True):
            if not _conversation_indexable(conversation, self._retention, now=now):
                continue
            cursor: str | None = None
            while True:
                page, cursor = await self._service.list_messages(
                    conversation.id,
                    limit=200,
                    cursor=cursor,
                )
                resources.extend(
                    _message_search_resource(message, conversation)
                    for message in page
                    if _message_indexable(message)
                )
                if cursor is None:
                    break
        return tuple(resources)


def install_conversation_search_services(
    control_plane: _ConversationSearchControlPlane,
    service: ConversationService,
) -> None:
    """Decorate the already-registered #72 resources with Search rebuild enumeration."""

    conversation_delegate = control_plane._resource_services.get(CONVERSATION_COLLECTION)
    message_delegate = control_plane._resource_services.get(CONVERSATION_MESSAGE_COLLECTION)
    if conversation_delegate is None or message_delegate is None:
        raise RuntimeError("canonical Conversation resources must be registered before Search")
    control_plane._resource_services[CONVERSATION_COLLECTION] = (
        _SearchableConversationResourceService(conversation_delegate, service)
    )
    control_plane._resource_services[CONVERSATION_MESSAGE_COLLECTION] = (
        _SearchableConversationMessageResourceService(message_delegate, service)
    )


async def conversation_search_result_allowed(
    control_plane: _ConversationSearchControlPlane,
    service: ConversationService,
    context: RequestContext,
    result: SearchResult,
) -> bool | None:
    """Re-authorize Conversation Search hits against canonical #72 state.

    ``None`` means this result belongs to another domain. Conversation results never
    authorize from SearchDocument scope metadata alone: the canonical Conversation is
    loaded again so stale or forged derived index state cannot reveal private existence.
    """

    if result.resource_type == "conversation":
        try:
            conversation = await service.get_conversation(result.resource_id)
        except (KeyError, ValueError):
            return False
        if not _conversation_indexable(
            conversation,
            ConversationRetentionManager(service),
            now=datetime.now(UTC),
        ):
            return False
        return await _canonical_conversation_allowed(
            control_plane,
            context,
            "conversation:list",
            conversation,
        )

    if result.resource_type == "conversation-message":
        try:
            message = await service.get_message(result.resource_id)
            conversation = await service.get_conversation(message.conversation_id)
        except (KeyError, ValueError):
            return False
        if not _message_indexable(message) or not _conversation_indexable(
            conversation,
            ConversationRetentionManager(service),
            now=datetime.now(UTC),
        ):
            return False
        return await _canonical_conversation_allowed(
            control_plane,
            context,
            "conversation-message:list",
            conversation,
        )

    return None


async def _canonical_conversation_allowed(
    control_plane: _ConversationSearchControlPlane,
    context: RequestContext,
    action: str,
    conversation: Conversation,
) -> bool:
    """Mirror #72's private-owner rule before consulting platform authorization."""

    if conversation.project_id is None and conversation.owner_ref != context.actor.principal_ref:
        return False
    return await control_plane._allowed(
        context,
        action,
        conversation.id,
        project_id=conversation.project_id,
    )


def _conversation_indexable(
    conversation: Conversation,
    retention: ConversationRetentionManager,
    *,
    now: datetime,
) -> bool:
    if conversation.status is ConversationStatus.TOMBSTONED:
        return False
    try:
        policy = retention.policy_for(conversation)
    except ValueError:
        # Invalid retention state must never make uncertain chat content discoverable.
        return False
    if (
        policy.mode is ConversationRetentionMode.UNTIL
        and policy.expires_at is not None
        and policy.expires_at <= now
    ):
        return False
    return True


def _message_indexable(message: ConversationMessage) -> bool:
    return message.status is not MessageStatus.TOMBSTONED


def _conversation_search_resource(conversation: Conversation) -> dict[str, JsonValue]:
    """Return the minimum canonical Conversation projection needed by global Search."""

    return {
        "id": conversation.id,
        "type": "conversation",
        "title": conversation.title,
        "summary": conversation.summary,
        "owner_ref": conversation.owner_ref,
        "project_id": conversation.project_id,
        "workspace_id": conversation.workspace_id,
        "status": conversation.status.value,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def _message_search_resource(
    message: ConversationMessage,
    conversation: Conversation,
) -> dict[str, JsonValue]:
    """Return a redaction-safe Message projection.

    Only user-visible text/markdown blocks become Search snippets. JSON blocks,
    reference metadata, arbitrary Message metadata, provider routing/session metadata,
    correlation identifiers and canonical attachment payloads are intentionally absent.
    """

    return {
        "id": message.id,
        "type": "conversation-message",
        "title": f"{message.role.value.title()} message in {conversation.title}",
        "summary": _permitted_message_text(message),
        "owner_ref": conversation.owner_ref,
        "project_id": conversation.project_id,
        "workspace_id": conversation.workspace_id,
        "status": message.status.value,
        "conversation_id": conversation.id,
        "role": message.role.value,
        "revision": message.revision,
        "created_at": message.created_at.isoformat(),
        "updated_at": (
            message.edited_at.isoformat()
            if message.edited_at is not None
            else message.created_at.isoformat()
        ),
    }


def _permitted_message_text(message: ConversationMessage) -> str:
    parts: list[str] = []
    for block in message.content:
        if block.kind not in {ContentKind.TEXT, ContentKind.MARKDOWN} or block.text is None:
            continue
        parts.append(block.text)
    return "\n".join(parts)[:500]


__all__ = [
    "conversation_search_result_allowed",
    "install_conversation_search_services",
]
