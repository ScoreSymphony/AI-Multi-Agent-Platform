"""Current public Control Plane composition with canonical Conversations (#72).

Conversation northbound ownership is registered explicitly. The public façade keeps
only the cross-domain Task bridge and transport ergonomics that cannot be represented as
plain resource/command registrations; it no longer composes Conversation behavior by
stacking a second Control Plane superclass with Notifications.
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
    ContextResolvingConversationResponseProvider,
    ConversationService,
)
from ai_multi_agent_platform.conversations.responses import ConversationResponseProvider
from ai_multi_agent_platform.data import FileProvider, KnowledgeProvider
from ai_multi_agent_platform.domain import TaskStatus, validate_id
from ai_multi_agent_platform.search import SearchResult

from .conversation_api import CONVERSATION_COLLECTIONS
from .conversation_composition import (
    _ALL_CONVERSATION_COMMANDS,
    _augment_conversation_openapi,
    _rewrite_conversation_request,
)
from .conversation_module import conversation_control_plane_module
from .conversation_response_streaming import (
    ConversationResponseASGI,
    augment_response_stream_openapi,
    is_response_stream_path,
)
from .conversation_retention import (
    CONVERSATION_EXPORT_COLLECTION,
    CONVERSATION_RETENTION_COMMANDS,
    augment_conversation_retention_openapi,
    rewrite_conversation_retention_request,
)
from .conversation_search import (
    conversation_search_result_allowed,
    install_conversation_search_services,
)
from .conversation_streaming import ConversationEventASGI
from .conversation_streaming_http import (
    _augment_stream_openapi,
    _is_conversation_stream_path,
)
from .http import HTTPRequest, HTTPResponse, _header
from .models import API_VERSION, APIException, RequestContext
from .module_registry import install_control_plane_modules
from .notifications_explicit_composition import (
    AuthenticatedControlPlaneHTTP as _NotificationAuthenticatedControlPlaneHTTP,
)
from .notifications_explicit_composition import ControlPlane as _NotificationControlPlane
from .notifications_explicit_composition import ControlPlaneASGI as _NotificationControlPlaneASGI
from .notifications_explicit_composition import ControlPlaneHTTP as _NotificationControlPlaneHTTP
from .notifications_explicit_composition import build_openapi as _build_notification_openapi

_ALL_CURRENT_CONVERSATION_COMMANDS = (
    *_ALL_CONVERSATION_COMMANDS,
    *CONVERSATION_RETENTION_COMMANDS,
)


class ControlPlane(_NotificationControlPlane):
    """Current Control Plane with Conversations installed through explicit ownership."""

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

        super().__init__(*args, **kwargs)
        self._conversation_service = conversation_service
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
            install_control_plane_modules(
                self,
                (
                    conversation_control_plane_module(
                        self,
                        conversation_service,
                        agent_service=conversation_agent_service,
                        file_provider=conversation_file_provider,
                        knowledge_provider=conversation_knowledge_provider,
                    ),
                ),
            )
            install_conversation_search_services(self, conversation_service)

    @property
    def conversation_service(self) -> ConversationService | None:
        return self._conversation_service

    async def _search_result_allowed(
        self,
        context: RequestContext,
        result: SearchResult,
    ) -> bool:
        service = self.conversation_service
        if service is not None:
            allowed = await conversation_search_result_allowed(self, service, context, result)
            if allowed is not None:
                return allowed
        return await super()._search_result_allowed(context, result)

    async def resume_task_from_conversation_input(
        self,
        context: RequestContext,
        *,
        task_id: str,
        conversation_id: str,
        message_id: str,
        request_payload_digest: str,
    ) -> dict[str, JsonValue]:
        """Resume one waiting canonical Task from referenced Conversation input."""

        validate_id(task_id, "task")
        validate_id(conversation_id, "conversation")
        validate_id(message_id, "message")
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
            )

        state = await self._kernel.get_task(task_id)
        await self._authorize_for_task(
            context,
            "task:resume",
            task_id,
            state,
            request_payload_digest=request_payload_digest,
        )
        resume_key = f"{context.idempotency_key}:conversation-resume:{message_id}"

        if state.status is not TaskStatus.WAITING:
            await self._kernel.resume_task(
                idempotency_key=resume_key,
                task_id=task_id,
                actor_ref=context.actor.principal_ref,
                source="control-plane:conversation",
            )
            return await self.get_task(context, task_id)

        input_ref: dict[str, JsonValue] = {
            "conversation_id": conversation_id,
            "message_id": message_id,
        }
        if state.task.metadata.get("conversation_input") != input_ref:
            await self._kernel.update_task(
                idempotency_key=(f"{context.idempotency_key}:conversation-input:{message_id}"),
                task_id=task_id,
                metadata={"conversation_input": input_ref},
                actor_ref=context.actor.principal_ref,
                source="control-plane:conversation",
            )

        await self._kernel.resume_task(
            idempotency_key=resume_key,
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane:conversation",
        )
        return await self.get_task(context, task_id)

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Persist Conversation linkage already authorized as part of Task creation."""

        resource = await super().create_task(context, payload)
        raw_metadata = payload.get("metadata")
        if not isinstance(raw_metadata, Mapping):
            return resource
        conversation_id = raw_metadata.get("conversation_id")
        message_id = raw_metadata.get("conversation_message_id")
        if not isinstance(conversation_id, str) or not isinstance(message_id, str):
            return resource
        validate_id(conversation_id, "conversation")
        validate_id(message_id, "message")
        task_id = resource.get("id")
        if not isinstance(task_id, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical task creation did not return a task id",
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
            )
        state = await self._kernel.get_task(task_id)
        metadata = dict(raw_metadata)
        if all(state.task.metadata.get(key) == value for key, value in metadata.items()):
            return await self.get_task(context, task_id)
        await self._kernel.update_task(
            idempotency_key=f"{context.idempotency_key}:conversation-link",
            task_id=task_id,
            metadata=metadata,
            actor_ref=context.actor.principal_ref,
            source="control-plane:conversation",
        )
        return await self.get_task(context, task_id)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        resource = await super().get_task(context, task_id)
        state = await self._kernel.get_task(task_id)
        if state.task.metadata:
            resource["metadata"] = cast(JsonValue, dict(state.task.metadata))
        return resource


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
