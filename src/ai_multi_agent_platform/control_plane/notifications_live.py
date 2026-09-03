"""Live Control Plane composition for canonical notifications (#75)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any, cast
from urllib.parse import parse_qsl
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.notifications import (
    NotificationEventSink,
    NotificationLiveHub,
    fanout_notification_event_sinks,
)

from .automation_runtime_composition import ControlPlaneASGI as _RuntimeControlPlaneASGI
from .http import (
    ASGISend,
    HTTPRequest,
    HTTPResponse,
    _decode_asgi_headers,
    _request_context,
    _send_response,
    _send_sse_error,
)
from .models import API_VERSION, APIException, RequestContext, api_exception_from_contract
from .notifications_composition import (
    NOTIFICATION_COLLECTION,
    ControlPlane as _BaseControlPlane,
)
from .notifications_composition import (
    ControlPlaneHTTP as _BaseControlPlaneHTTP,
)
from .notifications_composition import (
    _recipient_from_context,
)
from .notifications_composition import (
    build_openapi as _build_base_openapi,
)


class ControlPlane(_BaseControlPlane):
    """Current Control Plane plus recipient-scoped notification live updates."""

    def __init__(
        self,
        *args: Any,
        notification_live_hub: NotificationLiveHub | None = None,
        notification_event_sink: NotificationEventSink | None = None,
        **kwargs: Any,
    ) -> None:
        self._notification_live_hub = notification_live_hub or NotificationLiveHub()
        super().__init__(
            *args,
            notification_event_sink=fanout_notification_event_sinks(
                self._notification_live_hub.publish,
                notification_event_sink,
            ),
            **kwargs,
        )

    @property
    def notification_live_hub(self) -> NotificationLiveHub:
        return self._notification_live_hub

    async def subscribe_notifications(
        self,
        context: RequestContext,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        recipient = _recipient_from_context(context)
        await self._authorize(context, "notification:subscribe", NOTIFICATION_COLLECTION)
        stream = self._notification_live_hub.subscribe(
            recipient,
            after_event_id=after_event_id,
        )

        async def iterator() -> AsyncIterator[dict[str, JsonValue]]:
            async for event in stream:
                yield event.to_json()

        return iterator()


class ControlPlaneHTTP(_BaseControlPlaneHTTP):
    """HTTP mapping that advertises the Notification SSE endpoint."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        if request.method == "GET" and _is_notification_stream_path(request.path):
            return self._error_response(
                APIException(
                    status=406,
                    code="stream_transport_required",
                    message="use the SSE transport for this endpoint",
                ),
                request.headers.get("x-request-id") or f"request_{uuid4()}",
                request.headers.get("x-correlation-id") or f"request_{uuid4()}",
            )
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.endswith("/openapi.json")
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            return HTTPResponse(
                status=response.status,
                body=cast(
                    dict[str, JsonValue],
                    _augment_notification_live_openapi(
                        cast(dict[str, Any], deepcopy(response.body))
                    ),
                ),
                headers=dict(response.headers),
            )
        return response


class ControlPlaneASGI:
    """Runtime-complete ASGI composition plus recipient-scoped Notification SSE routing."""

    def __init__(self, http: Any) -> None:
        self._http = http
        self._inner = _RuntimeControlPlaneASGI(http)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        path = str(scope.get("path", "/"))
        if (
            scope.get("type") == "http"
            and str(scope.get("method", "GET")).upper() == "GET"
            and _is_notification_stream_path(path)
        ):
            await self._stream_notifications(scope, send)
            return
        await self._inner(scope, receive, send)

    async def _stream_notifications(self, scope: dict[str, Any], send: ASGISend) -> None:
        path = str(scope.get("path", "/"))
        headers = _decode_asgi_headers(scope.get("headers", []))
        query = dict(
            parse_qsl(
                bytes(scope.get("query_string", b"")).decode("utf-8"),
                keep_blank_values=True,
            )
        )
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        started = False
        try:
            prepared = self._http.prepare_stream_request(
                HTTPRequest(
                    method="GET",
                    path=path,
                    headers=headers,
                    query=query,
                ),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            if isinstance(prepared, HTTPResponse):
                await _send_response(prepared, send)
                return
            context = _request_context(prepared, request_id, correlation_id)
            control_plane = self._http._control_plane
            if not isinstance(control_plane, ControlPlane):
                raise APIException(
                    status=503,
                    code="notification_stream_unavailable",
                    message="notification live stream is not configured",
                )
            stream = await control_plane.subscribe_notifications(
                context,
                after_event_id=query.get("after_event_id"),
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"x-api-version", API_VERSION.encode("ascii")),
                        (b"x-request-id", request_id.encode()),
                        (b"x-correlation-id", correlation_id.encode()),
                    ],
                }
            )
            started = True
            async for event in stream:
                event_id = event.get("id")
                payload = json.dumps(event, separators=(",", ":"), default=str)
                prefix = f"id: {event_id}\n" if isinstance(event_id, str) else ""
                await send(
                    {
                        "type": "http.response.body",
                        "body": (
                            f"{prefix}event: notification.event\ndata: {payload}\n\n"
                        ).encode(),
                        "more_body": True,
                    }
                )
        except ContractError as exc:
            error = api_exception_from_contract(exc)
            if started:
                await _send_sse_error(error, request_id, correlation_id, send)
            else:
                await _send_response(
                    self._http._error_response(error, request_id, correlation_id),
                    send,
                )
        except APIException as exc:
            if started:
                await _send_sse_error(exc, request_id, correlation_id, send)
            else:
                await _send_response(
                    self._http._error_response(exc, request_id, correlation_id),
                    send,
                )


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    return _augment_notification_live_openapi(
        _build_base_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )
    )


def _augment_notification_live_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.get("paths")
    if isinstance(paths, dict):
        paths[f"/api/{API_VERSION}/notifications/stream"] = {
            "get": {
                "operationId": "streamNotifications",
                "summary": "Stream canonical notification attention updates",
                "parameters": [
                    {
                        "name": "after_event_id",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Recipient-scoped Server-Sent Events stream",
                        "content": {"text/event-stream": {}},
                    }
                },
            }
        }
    specification["x-notification-live-updates"] = {
        "transport": "sse",
        "route": f"/api/{API_VERSION}/notifications/stream",
        "event": "notification.event",
        "recovery": "refresh canonical notifications collection when cursor is unavailable",
    }
    return specification


def _is_notification_stream_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return segments == ["api", API_VERSION, "notifications", "stream"]
