"""Provider-neutral live Event projection for canonical conversations (issue #72)."""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from typing import Any, Protocol, cast
from urllib.parse import parse_qsl
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.conversations import Conversation, ConversationService
from ai_multi_agent_platform.conversations.lifecycle_projection import (
    project_conversation_lifecycle_event,
)
from ai_multi_agent_platform.domain import validate_id

from .automation_runtime_composition import ControlPlaneASGI as _CurrentControlPlaneASGI
from .http import (
    ASGIReceive,
    ASGISend,
    HTTPRequest,
    HTTPResponse,
    _request_context,
    _send_response,
    _send_sse_error,
)
from .models import API_VERSION, APIException, RequestContext, api_exception_from_contract

CONVERSATION_EVENT_STREAM_SUFFIX = ("events", "stream")
_CURSOR_VERSION = 1


class ConversationStreamingControlPlane(Protocol):
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

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]: ...


async def subscribe_conversation_events(
    control_plane: ConversationStreamingControlPlane,
    service: ConversationService,
    context: RequestContext,
    conversation_id: str,
    *,
    after_event_id: str | None = None,
) -> AsyncIterator[dict[str, JsonValue]]:
    """Project linked canonical Task/Run events into one Conversation stream.

    ``after_event_id`` is an opaque Conversation cursor. It records only canonical
    ``task_*`` -> ``event_*`` positions and therefore stays independent of any concrete
    EventProvider, orchestrator or execution backend.
    """

    validate_id(conversation_id, "conversation")
    conversation = await service.get_conversation(conversation_id)
    await _authorize_conversation_stream(control_plane, context, conversation)
    positions = _decode_cursor(after_event_id)
    unknown = sorted(set(positions).difference(conversation.task_ids))
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "conversation event cursor references Tasks not linked to this conversation",
            details={"task_ids": cast(list[JsonValue], unknown)},
        )

    async def iterator() -> AsyncIterator[dict[str, JsonValue]]:
        if not conversation.task_ids:
            return

        queue: asyncio.Queue[tuple[str, dict[str, JsonValue] | None, BaseException | None]] = (
            asyncio.Queue()
        )
        pumps = [
            asyncio.create_task(
                _pump_task_events(
                    control_plane,
                    context,
                    task_id,
                    positions.get(task_id),
                    queue,
                )
            )
            for task_id in conversation.task_ids
        ]
        active = set(conversation.task_ids)
        current_positions = dict(positions)
        try:
            while active:
                task_id, event, failure = await queue.get()
                if failure is not None:
                    raise failure
                if event is None:
                    active.discard(task_id)
                    continue
                event_id = event.get("id")
                if not isinstance(event_id, str):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "canonical Task event projection did not contain an event id",
                    )
                validate_id(event_id, "event")
                lifecycle = await project_conversation_lifecycle_event(
                    service,
                    conversation_id=conversation.id,
                    task_id=task_id,
                    event=event,
                )
                current_positions[task_id] = event_id
                cursor = _encode_cursor(current_positions)
                projection: dict[str, JsonValue] = {
                    "id": cursor,
                    "type": "conversation.task-event",
                    "conversation_id": conversation.id,
                    "task_id": task_id,
                    "authoritative": True,
                    "event": event,
                    "references": [reference.to_json() for reference in lifecycle.references],
                }
                if lifecycle.attention is not None:
                    projection["attention"] = lifecycle.attention
                yield projection
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            for pump in pumps:
                with suppress(asyncio.CancelledError):
                    await pump

    return iterator()


async def _pump_task_events(
    control_plane: ConversationStreamingControlPlane,
    context: RequestContext,
    task_id: str,
    after_event_id: str | None,
    queue: asyncio.Queue[tuple[str, dict[str, JsonValue] | None, BaseException | None]],
) -> None:
    try:
        stream = await control_plane.subscribe_task_events(
            context,
            task_id,
            after_event_id=after_event_id,
        )
        async for event in stream:
            await queue.put((task_id, event, None))
    except BaseException as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        await queue.put((task_id, None, exc))
    finally:
        await queue.put((task_id, None, None))


async def _authorize_conversation_stream(
    control_plane: ConversationStreamingControlPlane,
    context: RequestContext,
    conversation: Conversation,
) -> None:
    if conversation.project_id is None and conversation.owner_ref != context.actor.principal_ref:
        raise ContractError(ErrorCode.FORBIDDEN, "private conversation belongs to another actor")
    await control_plane._authorize(
        context,
        "conversation:read",
        conversation.id,
        project_id=conversation.project_id,
    )


def _encode_cursor(positions: Mapping[str, str]) -> str:
    payload = {
        "v": _CURSOR_VERSION,
        "positions": {key: positions[key] for key in sorted(positions)},
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None) -> dict[str, str]:
    if value is None or not value.strip():
        return {}
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode((value + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "conversation event cursor is invalid"
        ) from exc
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ContractError(ErrorCode.INVALID_REQUEST, "conversation event cursor is invalid")
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "conversation event cursor is invalid")
    positions: dict[str, str] = {}
    for task_id, event_id in raw_positions.items():
        if not isinstance(task_id, str) or not isinstance(event_id, str):
            raise ContractError(ErrorCode.INVALID_REQUEST, "conversation event cursor is invalid")
        validate_id(task_id, "task")
        validate_id(event_id, "event")
        positions[task_id] = event_id
    return positions


class ConversationEventASGI:
    """Intercept Conversation SSE while delegating every other ASGI scope unchanged."""

    def __init__(self, inner: Any, http: Any) -> None:
        self._inner = inner
        self._http = http
        self._control_plane = getattr(http, "_control_plane", None)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        if scope.get("type") != "http" or method != "GET" or not _is_stream_path(path):
            await self._inner(scope, receive, send)
            return
        await self._stream(path, scope, send)

    async def _stream(
        self,
        path: str,
        scope: dict[str, Any],
        send: ASGISend,
    ) -> None:
        headers = _decode_headers(scope.get("headers", []))
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
            conversation_id = _conversation_id_from_stream_path(path)
            stream_headers = dict(headers)
            stream_headers["x-request-id"] = request_id
            stream_headers["x-correlation-id"] = correlation_id
            prepared = self._http.prepare_stream_request(
                HTTPRequest(method="GET", path=path, headers=stream_headers, query=query),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            if isinstance(prepared, HTTPResponse):
                await _send_response(prepared, send)
                return
            context = _request_context(prepared, request_id, correlation_id)
            control_plane = self._control_plane
            service = getattr(control_plane, "conversation_service", None)
            if control_plane is None or not isinstance(service, ConversationService):
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    "conversation service is not configured",
                )
            cursor = query.get("after_event_id") or headers.get("last-event-id")
            stream = await subscribe_conversation_events(
                control_plane,
                service,
                context,
                conversation_id,
                after_event_id=cursor,
            )
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"connection", b"keep-alive"),
                        (b"x-api-version", API_VERSION.encode("ascii")),
                        (b"x-request-id", request_id.encode()),
                        (b"x-correlation-id", correlation_id.encode()),
                    ],
                }
            )
            started = True
            async for projection in stream:
                cursor_id = projection.get("id")
                if not isinstance(cursor_id, str):
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "conversation event projection did not contain a cursor id",
                    )
                payload = json.dumps(projection, separators=(",", ":"), default=str)
                frame = f"id: {cursor_id}\nevent: conversation.task-event\ndata: {payload}\n\n"
                await send(
                    {
                        "type": "http.response.body",
                        "body": frame.encode("utf-8"),
                        "more_body": True,
                    }
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
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


class ControlPlaneASGI:
    """Current ASGI composition plus canonical Conversation event streaming."""

    def __init__(self, http: Any) -> None:
        self._app = ConversationEventASGI(_CurrentControlPlaneASGI(http), http)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self._app(scope, receive, send)


def _is_stream_path(path: str) -> bool:
    segments = [segment for segment in path.split("/") if segment]
    return (
        len(segments) == 6
        and segments[:3] == ["api", API_VERSION, "conversations"]
        and tuple(segments[4:]) == CONVERSATION_EVENT_STREAM_SUFFIX
    )


def _conversation_id_from_stream_path(path: str) -> str:
    if not _is_stream_path(path):
        raise APIException(status=404, code="not_found", message="route not found")
    return [segment for segment in path.split("/") if segment][3]


def _decode_headers(raw_headers: Any) -> dict[str, str]:
    decoded: dict[str, str] = {}
    for key, value in raw_headers:
        decoded[bytes(key).decode("latin-1").lower()] = bytes(value).decode("latin-1")
    return decoded


__all__ = [
    "CONVERSATION_EVENT_STREAM_SUFFIX",
    "ControlPlaneASGI",
    "ConversationEventASGI",
    "subscribe_conversation_events",
]
