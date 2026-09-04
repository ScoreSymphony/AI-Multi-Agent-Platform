"""HTTP/OpenAPI composition for canonical Conversation SSE (issue #72)."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue

from .conversation_composition import ControlPlaneHTTP as _ConversationControlPlaneHTTP
from .conversation_composition import build_openapi as _build_conversation_openapi
from .conversation_streaming import ControlPlaneASGI
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, APIException


class ControlPlaneHTTP(_ConversationControlPlaneHTTP):
    """Expose the SSE transport contract without treating the stream as JSON HTTP."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        if request.method == "GET" and _is_conversation_stream_path(request.path):
            request_id = _header(request, "x-request-id") or f"request_{uuid4()}"
            correlation_id = _header(request, "x-correlation-id") or request_id
            return self._error_response(
                APIException(
                    status=406,
                    code="stream_transport_required",
                    message="use the SSE transport for this endpoint",
                ),
                request_id,
                correlation_id,
            )

        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
            and response.body.get("x-conversation-task-centric") is True
        ):
            return HTTPResponse(
                status=response.status,
                body=cast(
                    dict[str, JsonValue],
                    _augment_stream_openapi(cast(dict[str, Any], response.body)),
                ),
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
) -> dict[str, Any]:
    specification = _build_conversation_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
        include_conversations=include_conversations,
    )
    if not include_conversations:
        return specification
    return _augment_stream_openapi(specification)


def _augment_stream_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return specification
    paths[f"/api/{API_VERSION}/conversations/{{conversation_id}}/events/stream"] = {
        "get": {
            "operationId": "streamConversationEvents",
            "description": (
                "Stream authoritative canonical Task/Run events linked to a Conversation"
            ),
            "responses": {
                "200": {"description": "Server-Sent Events using opaque Conversation cursors"},
                "401": {"description": "Authentication required"},
                "403": {"description": "Conversation or linked Task access denied"},
            },
        }
    }
    specification["x-conversation-event-stream-provider-neutral"] = True
    return specification


def _is_conversation_stream_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) == 6
        and segments[:3] == ["api", API_VERSION, "conversations"]
        and segments[4:] == ["events", "stream"]
    )


def _header(request: HTTPRequest, name: str) -> str | None:
    target = name.casefold()
    for key, value in request.headers.items():
        if key.casefold() == target:
            return value
    return None


__all__ = ["ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
