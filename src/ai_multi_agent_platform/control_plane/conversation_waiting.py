"""Explicit waiting-Task input bridge for canonical conversations (issue #72)."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import ConversationService, MessageRole

from .extensions import CommandHandler
from .models import RequestContext
from .service import _payload_digest

CONVERSATION_RESUME_TASK_COMMAND = "conversation.message.resume-task"


class WaitingConversationControlPlane(Protocol):
    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None: ...

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]: ...

    async def resume_task_from_conversation_input(
        self,
        context: RequestContext,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
        request_payload_digest: str,
    ) -> dict[str, JsonValue]: ...

    def register_command(self, command: str, handler: CommandHandler) -> None: ...


class ConversationWaitingCommandHandler:
    """Bridge explicit user input to an already-waiting canonical Task."""

    def __init__(
        self,
        service: ConversationService,
        control_plane: WaitingConversationControlPlane,
    ) -> None:
        self._service = service
        self._control_plane = control_plane

    async def resume_task(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        message = await self._service.get_message(resource_ref)
        if message.role is not MessageRole.USER:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "only an authenticated user message can provide waiting Task input",
            )
        conversation = await self._service.get_conversation(message.conversation_id)
        if (
            conversation.project_id is None
            and conversation.owner_ref != context.actor.principal_ref
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN, "private conversation belongs to another actor"
            )

        digest = _payload_digest(payload)
        await self._control_plane._authorize(
            context,
            "conversation-message:modify",
            message.id,
            project_id=conversation.project_id,
            request_payload_digest=digest,
        )

        task_id = _required_string(payload, "task_id")
        task = await self._control_plane.get_task(context, task_id)
        _require_same_project(conversation.project_id, task.get("project_id"))

        # Persist canonical provenance before changing lifecycle state. This is only a
        # reference relationship; the message content itself is never copied into Task
        # state and linking a message does not imply execution or resume.
        linked_conversation = await self._service.link_task(
            conversation_id=conversation.id,
            task_id=task_id,
            message_id=message.id,
        )
        linked_message = await self._service.get_message(message.id)

        resumed_task = await self._control_plane.resume_task_from_conversation_input(
            context,
            task_id=task_id,
            conversation_id=conversation.id,
            message_id=message.id,
            request_payload_digest=digest,
        )
        return {
            "id": task_id,
            "type": "conversation-task-input",
            "conversation_id": linked_conversation.id,
            "message_id": linked_message.id,
            "task": resumed_task,
        }


def register_conversation_waiting_control_plane(
    control_plane: WaitingConversationControlPlane,
    service: ConversationService,
) -> None:
    handler = ConversationWaitingCommandHandler(service, control_plane)
    control_plane.register_command(CONVERSATION_RESUME_TASK_COMMAND, handler.resume_task)


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def _require_same_project(conversation_project: str | None, task_project: JsonValue) -> None:
    if conversation_project is None or task_project is None:
        return
    if not isinstance(task_project, str) or task_project != conversation_project:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "task reference crosses the conversation project boundary",
        )


__all__ = [
    "CONVERSATION_RESUME_TASK_COMMAND",
    "ConversationWaitingCommandHandler",
    "register_conversation_waiting_control_plane",
]
