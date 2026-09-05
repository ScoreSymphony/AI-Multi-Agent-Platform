"""Current public Control Plane composition with canonical Conversations (#72).

This bridge deliberately composes Conversations above the newest public Control Plane
instead of pinning the Conversation domain to an older intermediate composition layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import (
    RESERVED_CONVERSATION_METADATA_KEYS,
    ContextResolvingConversationResponseProvider,
    Conversation,
    ConversationService,
    ReferenceKind,
    ResourceReference,
)
from ai_multi_agent_platform.conversations.responses import ConversationResponseProvider
from ai_multi_agent_platform.data import FileProvider, KnowledgeProvider

from .conversation_api import CONVERSATION_COLLECTIONS, ConversationCommandHandlers
from .conversation_composition import (
    _ALL_CONVERSATION_COMMANDS,
    _augment_conversation_openapi,
    _rewrite_conversation_request,
)
from .conversation_composition import ControlPlane as _ConversationControlPlane
from .conversation_knowledge import validate_conversation_knowledge_reference
from .conversation_response_streaming import (
    ConversationResponseASGI,
    augment_response_stream_openapi,
    is_response_stream_path,
)
from .conversation_retention import (
    CONVERSATION_EXPORT_COLLECTION,
    CONVERSATION_RETENTION_COMMANDS,
    augment_conversation_retention_openapi,
    register_conversation_retention_control_plane,
    rewrite_conversation_retention_request,
)
from .conversation_streaming import ConversationEventASGI
from .conversation_streaming_http import (
    _augment_stream_openapi,
    _is_conversation_stream_path,
)
from .extensions import (
    CommandHandler,
    ResourceService,
    _reject_private_payload,
    _validate_command_name,
)
from .http import HTTPRequest, HTTPResponse, _header
from .models import API_VERSION, APIException, RequestContext
from .notifications_plugin_composition import (
    AuthenticatedControlPlaneHTTP as _NotificationAuthenticatedControlPlaneHTTP,
)
from .notifications_plugin_composition import ControlPlane as _NotificationControlPlane
from .notifications_plugin_composition import ControlPlaneASGI as _NotificationControlPlaneASGI
from .notifications_plugin_composition import ControlPlaneHTTP as _NotificationControlPlaneHTTP
from .notifications_plugin_composition import build_openapi as _build_notification_openapi

_ALL_CURRENT_CONVERSATION_COMMANDS = (
    *_ALL_CONVERSATION_COMMANDS,
    *CONVERSATION_RETENTION_COMMANDS,
)


class _KnowledgeConversationCommandHandlers(ConversationCommandHandlers):
    """Conversation handlers that add Knowledge and reserved-metadata boundaries."""

    def __init__(
        self,
        service: ConversationService,
        control_plane: ControlPlane,
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
        """Snapshot omitted Agent/Team revisions before the durable Conversation is created."""

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


class ControlPlane(_ConversationControlPlane, _NotificationControlPlane):
    """Conversation behavior composed cooperatively above the current Notification stack."""

    def __init__(
        self,
        *args: Any,
        conversation_service: ConversationService | None = None,
        conversation_agent_service: AgentService | None = None,
        conversation_file_provider: FileProvider | None = None,
        conversation_knowledge_provider: KnowledgeProvider | None = None,
        conversation_response_provider: ConversationResponseProvider | None = None,
        **kwargs: Any,
    ) -> None:
        if conversation_service is None and any(
            dependency is not None
            for dependency in (
                conversation_agent_service,
                conversation_file_provider,
                conversation_knowledge_provider,
                conversation_response_provider,
            )
        ):
            raise ValueError("conversation dependencies require conversation_service")
        if conversation_service is not None:
            supplied_resources = kwargs.get("resource_services")
            if isinstance(supplied_resources, Mapping) and (
                CONVERSATION_EXPORT_COLLECTION in supplied_resources
            ):
                raise ValueError(
                    "resource_services conflict with canonical conversation export route"
                )
            supplied_commands = kwargs.get("command_handlers")
            if isinstance(supplied_commands, Mapping):
                conflicts = sorted(
                    set(supplied_commands).intersection(CONVERSATION_RETENTION_COMMANDS)
                )
                if conflicts:
                    raise ValueError(
                        "command_handlers conflict with canonical conversation retention commands: "
                        f"{conflicts!r}"
                    )

        self._installing_conversation_retention = False
        super().__init__(
            *args,
            conversation_service=conversation_service,
            conversation_agent_service=conversation_agent_service,
            conversation_file_provider=conversation_file_provider,
            **kwargs,
        )
        self._conversation_knowledge_provider = conversation_knowledge_provider
        self.conversation_response_provider = (
            ContextResolvingConversationResponseProvider(
                conversation_response_provider,
                file_provider=conversation_file_provider,
                knowledge_provider=conversation_knowledge_provider,
            )
            if conversation_response_provider is not None
            else None
        )

        if conversation_service is not None:
            # The intermediate Conversation composition installs the canonical handlers.
            # Replace only handlers that need current-domain extensions; the underlying
            # Conversation lifecycle and persistence path remains the same.
            handlers = _KnowledgeConversationCommandHandlers(
                conversation_service,
                self,
                agent_service=conversation_agent_service,
                file_provider=conversation_file_provider,
                knowledge_provider=conversation_knowledge_provider,
            )
            self._command_handlers["conversation.create"] = handlers.create_conversation
            self._command_handlers["conversation.message.add"] = handlers.add_message
            self._installing_conversation_retention = True
            try:
                register_conversation_retention_control_plane(self, conversation_service)
            finally:
                self._installing_conversation_retention = False

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in CONVERSATION_RETENTION_COMMANDS or self.conversation_service is None:
            return await super().execute_command(context, command, resource_ref, payload)
        _validate_command_name(command)
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        handler = self._command_handlers.get(command)
        if handler is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"canonical command is not registered: {command}",
                details={"command": command},
            )
        result = await handler(context, resource_ref, payload or {})
        _reject_private_payload(result)
        return result

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if (
            collection == CONVERSATION_EXPORT_COLLECTION
            and not self._installing_conversation_retention
        ):
            raise ValueError(
                f"extension collection conflicts with canonical conversation route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if (
            command in CONVERSATION_RETENTION_COMMANDS
            and not self._installing_conversation_retention
        ):
            raise ValueError(
                f"extension command conflicts with canonical conversation command: {command}"
            )
        super().register_command(command, handler)


class ControlPlaneHTTP(_NotificationControlPlaneHTTP):
    """Add ergonomic Conversation routes to the current public HTTP composition."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        if getattr(self._control_plane, "conversation_service", None) is None:
            return await super().handle(request)

        if (
            request.method == "GET"
            and _is_conversation_stream_path(request.path)
            or request.method == "POST"
            and is_response_stream_path(request.path)
        ):
            request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
            correlation_id = _header(request.headers, "x-correlation-id") or request_id
            return self._error_response(
                APIException(
                    status=406,
                    code="stream_transport_required",
                    message="use the SSE transport for this endpoint",
                ),
                request_id,
                correlation_id,
            )

        retention_rewritten = rewrite_conversation_retention_request(request)
        if retention_rewritten is request:
            rewritten, created = _rewrite_conversation_request(request)
        else:
            rewritten, created = retention_rewritten, False
        response = await super().handle(rewritten)
        if created and response.status == 200:
            response = HTTPResponse(status=201, body=response.body, headers=dict(response.headers))

        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_conversation_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
            specification = _augment_stream_openapi(specification)
            specification = augment_response_stream_openapi(specification)
            specification = augment_conversation_retention_openapi(specification)
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response


class AuthenticatedControlPlaneHTTP(_NotificationAuthenticatedControlPlaneHTTP):
    """Authenticate first, then expose the current Conversation-aware HTTP surface."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._current_http = ControlPlaneHTTP(cast(ControlPlane, self._control_plane))


class ControlPlaneASGI:
    """Conversation SSE above the complete current Notification/Plugin/Automation ASGI stack."""

    def __init__(self, http: Any) -> None:
        events = ConversationEventASGI(_NotificationControlPlaneASGI(http), http)
        self._app = ConversationResponseASGI(events, http)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self._app(scope, receive, send)


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
) -> dict[str, Any]:
    """Build the newest public schema and optionally include canonical Conversations."""

    if not include_conversations:
        return _build_notification_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )

    collections = tuple(
        sorted(
            set(
                (
                    *extension_collections,
                    *CONVERSATION_COLLECTIONS,
                    CONVERSATION_EXPORT_COLLECTION,
                )
            )
        )
    )
    commands = tuple(sorted(set((*extension_commands, *_ALL_CURRENT_CONVERSATION_COMMANDS))))
    specification = _build_notification_openapi(
        extension_collections=collections,
        extension_commands=commands,
    )
    specification = _augment_conversation_openapi(specification)
    specification = _augment_stream_openapi(specification)
    specification = augment_response_stream_openapi(specification)
    return augment_conversation_retention_openapi(specification)


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "build_openapi",
]
