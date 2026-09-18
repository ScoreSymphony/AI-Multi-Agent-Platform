"""Explicit Control Plane ownership for the canonical Conversation domain.

The Conversation lifecycle remains owned by ``ConversationService``.  This module only
adapts its northbound resources and commands into the canonical Control Plane registry so
current product composition does not need a Conversation ControlPlane superclass.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    RESERVED_CONVERSATION_METADATA_KEYS,
    Conversation,
    ConversationService,
    ReferenceKind,
    ResourceReference,
)
from ai_multi_agent_platform.data import FileProvider, KnowledgeProvider

from .conversation_api import (
    CONVERSATION_COLLECTION,
    CONVERSATION_COMMANDS,
    CONVERSATION_MESSAGE_COLLECTION,
    ConversationCommandHandlers,
    ConversationControlPlane,
    ConversationMessageResourceService,
    ConversationResourceService,
)
from .conversation_knowledge import validate_conversation_knowledge_reference
from .conversation_retention import (
    CONVERSATION_EXPORT_COLLECTION,
    CONVERSATION_RETENTION_COMMANDS,
    ConversationExportResourceService,
    ConversationRetentionCommandHandlers,
)
from .conversation_waiting import (
    CONVERSATION_RESUME_TASK_COMMAND,
    ConversationWaitingCommandHandler,
    WaitingConversationControlPlane,
)
from .extensions import CommandHandler, ControlPlaneModule
from .models import RequestContext

CONVERSATION_MODULE = "conversations"


class _CurrentConversationCommandHandlers(ConversationCommandHandlers):
    """Conversation handlers with the current Knowledge and metadata boundaries."""

    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
        *,
        agent_service: AgentService | None,
        file_provider: FileProvider | None,
        knowledge_provider: KnowledgeProvider | None,
    ) -> None:
        super().__init__(
            service,
            control_plane,
            agent_service=agent_service,
            file_provider=file_provider,
        )
        self._knowledge_provider = knowledge_provider

    async def create_conversation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        metadata = payload.get("metadata")
        if isinstance(metadata, Mapping):
            if "target" in metadata:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "conversation target metadata is platform-managed; use the top-level target",
                    details={"field": "target"},
                )
            reserved = sorted(RESERVED_CONVERSATION_METADATA_KEYS.intersection(metadata))
            if reserved:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "conversation retention metadata is platform-managed",
                    details={"fields": cast(JsonValue, reserved)},
                )
        return await super().create_conversation(
            context,
            resource_ref,
            self._pin_agent_revisions(payload),
        )

    def _pin_agent_revisions(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        service = self._agent_service
        if service is None:
            return payload
        normalized = dict(payload)
        for field_name in ("target", "default_agent"):
            raw = normalized.get(field_name)
            if not isinstance(raw, Mapping):
                continue
            kind = raw.get("kind")
            resource_id = raw.get("id")
            revision = raw.get("revision")
            if revision is not None or not isinstance(resource_id, str):
                continue
            if kind == "agent":
                revision = service.get_agent_revision(resource_id).revision
            elif kind == "agent_team":
                revision = service.get_team_revision(resource_id).revision
            else:
                continue
            resolved = dict(raw)
            resolved["revision"] = revision
            normalized[field_name] = cast(JsonValue, resolved)
        return normalized

    async def _validate_reference(
        self,
        context: RequestContext,
        conversation: Conversation,
        reference: ResourceReference,
    ) -> None:
        if reference.kind is ReferenceKind.KNOWLEDGE:
            await validate_conversation_knowledge_reference(
                self._knowledge_provider,
                context,
                conversation,
                reference,
            )
            return
        await super()._validate_reference(context, conversation, reference)


async def _handler_owned_authorization(
    context: RequestContext,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> None:
    """Preserve resource-aware Conversation authorization inside canonical handlers.

    The legacy Conversation composition deliberately bypassed the generic
    ``_authorize(command, resource_ref)`` preflight because each handler authorizes
    against the canonical Conversation/Message/Task relationship.  Declaring that
    boundary explicitly preserves the existing security semantics without an
    ``execute_command`` subclass override.
    """

    del context, resource_ref, payload


def conversation_control_plane_module(
    control_plane: ConversationControlPlane | WaitingConversationControlPlane,
    service: ConversationService,
    *,
    agent_service: AgentService | None = None,
    file_provider: FileProvider | None = None,
    knowledge_provider: KnowledgeProvider | None = None,
) -> ControlPlaneModule:
    """Build the complete explicitly owned northbound Conversation contribution."""

    current = _CurrentConversationCommandHandlers(
        service,
        cast(ConversationControlPlane, control_plane),
        agent_service=agent_service,
        file_provider=file_provider,
        knowledge_provider=knowledge_provider,
    )
    waiting = ConversationWaitingCommandHandler(
        service,
        cast(WaitingConversationControlPlane, control_plane),
    )
    retention = ConversationRetentionCommandHandlers(
        service,
        cast(ConversationControlPlane, control_plane),
    )

    async def create_conversation(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        result = await current.create_conversation(context, resource_ref, payload)
        return await _normalize_conversation_task_links(
            service,
            command="conversation.create",
            resource_ref=resource_ref,
            payload=payload,
            result=result,
        )

    async def attach_message_to_task(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        result = await current.attach_message_to_task(context, resource_ref, payload)
        return await _normalize_conversation_task_links(
            service,
            command="conversation.message.attach-task",
            resource_ref=resource_ref,
            payload=payload,
            result=result,
        )

    handlers: dict[str, CommandHandler] = {
        "conversation.create": create_conversation,
        "conversation.archive": current.archive_conversation,
        "conversation.reopen": current.reopen_conversation,
        "conversation.message.add": current.add_message,
        "conversation.message.create-task": current.create_task_from_message,
        "conversation.message.attach-task": attach_message_to_task,
        "conversation.link-run": current.link_run,
        "conversation.link-artifact": current.link_artifact,
        CONVERSATION_RESUME_TASK_COMMAND: waiting.resume_task,
        "conversation.retention.set": retention.set_retention,
        "conversation.delete": retention.delete_conversation,
    }
    expected = frozenset(
        (
            *CONVERSATION_COMMANDS,
            CONVERSATION_RESUME_TASK_COMMAND,
            *CONVERSATION_RETENTION_COMMANDS,
        )
    )
    if frozenset(handlers) != expected:
        raise RuntimeError("explicit Conversation module command inventory is incomplete")

    return ControlPlaneModule(
        name=CONVERSATION_MODULE,
        resource_services={
            CONVERSATION_COLLECTION: ConversationResourceService(
                service,
                cast(ConversationControlPlane, control_plane),
            ),
            CONVERSATION_MESSAGE_COLLECTION: ConversationMessageResourceService(
                service,
                cast(ConversationControlPlane, control_plane),
            ),
            CONVERSATION_EXPORT_COLLECTION: ConversationExportResourceService(
                service,
                cast(ConversationControlPlane, control_plane),
            ),
        },
        command_handlers=handlers,
        command_authorizers={command: _handler_owned_authorization for command in handlers},
    )


async def _normalize_conversation_task_links(
    service: ConversationService,
    *,
    command: str,
    resource_ref: str,
    payload: Mapping[str, JsonValue],
    result: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Preserve the canonical Task/Conversation linkage invariant after commands."""

    if command == "conversation.create":
        target = payload.get("target")
        if isinstance(target, Mapping) and target.get("kind") == "task":
            task_id = target.get("id")
            conversation_id = result.get("id")
            if isinstance(task_id, str) and isinstance(conversation_id, str):
                linked = await service.link_task(
                    conversation_id=conversation_id,
                    task_id=task_id,
                )
                normalized = linked.to_json()
                normalized["type"] = "conversation"
                return normalized

    if command == "conversation.message.attach-task":
        task_id = payload.get("task_id")
        if isinstance(task_id, str):
            message = await service.get_message(resource_ref)
            await service.link_task(
                conversation_id=message.conversation_id,
                task_id=task_id,
                message_id=message.id,
            )
    return result


__all__ = [
    "CONVERSATION_MODULE",
    "conversation_control_plane_module",
]
