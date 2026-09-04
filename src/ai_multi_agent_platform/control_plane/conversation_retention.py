"""Control Plane retention, deletion and export surface for Conversations (#72)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    ConversationRetentionManager,
    ConversationRetentionPolicy,
    ConversationService,
)

from .conversation_api import (
    ConversationControlPlane,
    _authorize_conversation,
    _conversation_allowed,
    _conversation_resource,
)
from .extensions import CommandHandler, ResourceService
from .http import HTTPRequest
from .models import API_VERSION, PageQuery, RequestContext
from .service import _payload_digest

CONVERSATION_EXPORT_COLLECTION = "conversation-exports"
CONVERSATION_RETENTION_COMMANDS = (
    "conversation.retention.set",
    "conversation.delete",
)


class ConversationExportResourceService(ResourceService):
    """Read-only portable export of Conversation-owned state and canonical references."""

    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
    ) -> None:
        self._service = service
        self._control_plane = control_plane
        self._retention = ConversationRetentionManager(service)

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        filters = query.filters or {}
        conversations = await self._service.list_conversations(
            owner_ref=filters.get("owner_ref"),
            project_id=filters.get("project_id"),
            workspace_id=filters.get("workspace_id"),
            include_archived=True,
        )
        exports: list[dict[str, JsonValue]] = []
        for conversation in conversations:
            if await _conversation_allowed(
                self._control_plane,
                context,
                "conversation:export",
                conversation,
            ):
                exports.append(await self._retention.export(conversation.id))
        return tuple(exports)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_id)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:export",
            conversation,
        )
        return await self._retention.export(conversation.id)


class ConversationRetentionCommandHandlers:
    def __init__(
        self,
        service: ConversationService,
        control_plane: ConversationControlPlane,
    ) -> None:
        self._service = service
        self._control_plane = control_plane
        self._retention = ConversationRetentionManager(service)

    async def set_retention(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:modify",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        policy = _retention_policy(payload)
        updated = await self._retention.set_policy(conversation.id, policy)
        resource = _conversation_resource(updated)
        resource["retention"] = policy.to_json()
        return resource

    async def delete_conversation(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "conversation.delete does not accept a payload",
            )
        conversation = await self._service.get_conversation(resource_ref)
        await _authorize_conversation(
            self._control_plane,
            context,
            "conversation:delete",
            conversation,
            request_payload_digest=_payload_digest(payload),
        )
        return _conversation_resource(await self._retention.tombstone(conversation.id))


def register_conversation_retention_control_plane(
    control_plane: ConversationControlPlane,
    service: ConversationService,
) -> None:
    control_plane.register_resource_service(
        CONVERSATION_EXPORT_COLLECTION,
        ConversationExportResourceService(service, control_plane),
    )
    handlers = ConversationRetentionCommandHandlers(service, control_plane)
    registrations: dict[str, CommandHandler] = {
        "conversation.retention.set": handlers.set_retention,
        "conversation.delete": handlers.delete_conversation,
    }
    for command, handler in registrations.items():
        control_plane.register_command(command, handler)


def rewrite_conversation_retention_request(request: HTTPRequest) -> HTTPRequest:
    prefix = f"/api/{API_VERSION}"
    path = request.path.rstrip("/") or "/"
    if not path.startswith(prefix):
        return request
    relative = path[len(prefix) :] or "/"
    segments = [segment for segment in relative.split("/") if segment]

    command: str | None = None
    resource_ref: str | None = None
    if request.method == "GET" and len(segments) == 3:
        if segments[0] == "conversations" and segments[2] == "export":
            return HTTPRequest(
                method="GET",
                path=f"{prefix}/{CONVERSATION_EXPORT_COLLECTION}/{segments[1]}",
                headers=request.headers,
                query=request.query,
                body=request.body,
                trusted_actor=request.trusted_actor,
            )
    if request.method == "DELETE" and len(segments) == 2 and segments[0] == "conversations":
        command = "conversation.delete"
        resource_ref = segments[1]
    elif (
        request.method == "POST"
        and len(segments) == 2
        and segments[0] == "conversations"
        and ":" in segments[1]
    ):
        conversation_id, operation = segments[1].rsplit(":", 1)
        if operation == "set-retention":
            command = "conversation.retention.set"
            resource_ref = conversation_id

    if command is None or resource_ref is None:
        return request
    body = dict(request.body)
    body["resource_ref"] = resource_ref
    return HTTPRequest(
        method="POST",
        path=f"{prefix}/commands/{command}",
        headers=request.headers,
        query=request.query,
        body=body,
        trusted_actor=request.trusted_actor,
    )


def augment_conversation_retention_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    prefix = f"/api/{API_VERSION}"
    paths = cast(dict[str, Any], specification.setdefault("paths", {}))
    conversation_path = cast(
        dict[str, Any],
        paths.setdefault(f"{prefix}/conversations/{{conversation_id}}", {}),
    )
    conversation_path["delete"] = _operation(
        "deleteConversation",
        (
            "Tombstone and redact Conversation-owned chat state without deleting "
            "canonical Task/Run history."
        ),
        idempotent=True,
    )
    paths[f"{prefix}/conversations/{{conversation_id}}:set-retention"] = {
        "post": _operation(
            "setConversationRetention",
            "Set durable or time-bounded Conversation retention.",
            idempotent=True,
        )
    }
    paths[f"{prefix}/conversations/{{conversation_id}}/export"] = {
        "get": _operation(
            "exportConversation",
            (
                "Export Conversation-owned state and canonical references without "
                "expanding external resources."
            ),
            idempotent=False,
        )
    }
    return specification


def _operation(operation_id: str, description: str, *, idempotent: bool) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = [
        {
            "name": "conversation_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string", "pattern": "^conversation_"},
        }
    ]
    if idempotent:
        parameters.append(
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 1},
            }
        )
    return {
        "operationId": operation_id,
        "description": description,
        "parameters": parameters,
        "responses": {
            "200": {"description": "Success"},
            "400": {"description": "Invalid request"},
            "401": {"description": "Authentication required"},
            "403": {"description": "Forbidden"},
            "404": {"description": "Not found"},
        },
    }


def _retention_policy(payload: Mapping[str, JsonValue]) -> ConversationRetentionPolicy:
    allowed = {"mode", "expires_at"}
    unexpected = sorted(set(payload).difference(allowed))
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported conversation retention fields",
            details={"fields": cast(JsonValue, unexpected)},
        )
    try:
        return ConversationRetentionPolicy.from_json(payload)
    except (TypeError, ValueError) as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc


__all__ = [
    "CONVERSATION_EXPORT_COLLECTION",
    "CONVERSATION_RETENTION_COMMANDS",
    "ConversationExportResourceService",
    "ConversationRetentionCommandHandlers",
    "augment_conversation_retention_openapi",
    "register_conversation_retention_control_plane",
    "rewrite_conversation_retention_request",
]
