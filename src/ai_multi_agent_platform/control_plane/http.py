"""Framework-neutral HTTP mapping and dependency-free ASGI transport."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast
from urllib.parse import parse_qsl
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    API_VERSION,
    SUPPORTED_API_VERSIONS,
    ActorContext,
    APIError,
    APIException,
    OwnerType,
    PageQuery,
    RequestContext,
    api_exception_from_contract,
)
from .openapi import build_openapi
from .service import ControlPlane


@dataclass(frozen=True, slots=True)
class HTTPRequest:
    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: dict[str, JsonValue] = field(default_factory=dict)
    trusted_actor: ActorContext | None = None


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status: int
    body: JsonValue
    headers: dict[str, str] = field(default_factory=dict)


class ControlPlaneHTTP:
    """Map `/api/v1` to the application service without owning lifecycle truth."""

    def __init__(self, control_plane: ControlPlane) -> None:
        self._control_plane = control_plane

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            version, relative = _split_version(request.path)
            _require_supported_version(version)

            if request.method == "GET" and relative == "/openapi.json":
                return self._response(200, build_openapi(), request_id, correlation_id)
            if request.method == "GET" and relative in {"/health", "/readiness"}:
                health = await self._control_plane.health()
                status = 200 if relative == "/health" or health.get("ready") is True else 503
                return self._response(status, health, request_id, correlation_id)
            if request.method == "GET" and relative in {"", "/"}:
                return self._response(
                    200,
                    {
                        "api_version": API_VERSION,
                        "resources": [
                            "projects",
                            "workspaces",
                            "tasks",
                            "plans",
                            "steps",
                            "runs",
                            "artifacts",
                            "results",
                            "timeline",
                            "model-providers",
                            "models",
                        ],
                        "openapi": f"/api/{API_VERSION}/openapi.json",
                        "live_updates": "sse",
                    },
                    request_id,
                    correlation_id,
                )

            context = _request_context(request, request_id, correlation_id)
            query = _page_query(request.query)
            segments = [segment for segment in relative.split("/") if segment]
            if not segments:
                raise APIException(status=404, code="not_found", message="route not found")

            if segments[0] == "projects":
                return await self._projects(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "workspaces":
                return await self._workspaces(
                    request,
                    context,
                    query,
                    segments,
                    request_id,
                    correlation_id,
                )
            if segments[0] == "tasks":
                return await self._tasks(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "runs":
                return await self._runs(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "model-providers":
                return await self._model_providers(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] == "models":
                return await self._models(
                    request, context, query, segments, request_id, correlation_id
                )
            if segments[0] in {"plans", "steps", "artifacts", "results"}:
                return await self._references(
                    request,
                    context,
                    query,
                    cast(Literal["plans", "steps", "artifacts", "results"], segments[0]),
                    segments,
                    request_id,
                    correlation_id,
                )
            raise APIException(status=404, code="not_found", message="route not found")
        except ContractError as exc:
            return self._error_response(
                api_exception_from_contract(exc),
                request_id,
                correlation_id,
            )
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)
        except (ValueError, TypeError) as exc:
            return self._error_response(
                APIException(status=400, code="invalid_request", message=str(exc)),
                request_id,
                correlation_id,
            )

    def prepare_stream_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest | HTTPResponse:
        """Prepare an SSE request before a RequestContext is constructed.

        The base transport is intentionally a no-op. Authentication wrappers override this
        hook so event streams cannot bypass the same identity boundary used by normal HTTP
        requests.
        """

        return request

    async def _projects(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "POST":
            _require_json(request)
            item = await self._control_plane.create_project(context, request.body)
            return self._response(201, item, request_id, correlation_id)
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_projects(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_project(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _workspaces(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "POST":
            _require_json(request)
            item = await self._control_plane.create_workspace(context, request.body)
            return self._response(201, item, request_id, correlation_id)
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_workspaces(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_workspace(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _tasks(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "POST":
            _require_json(request)
            item = await self._control_plane.create_task(context, request.body)
            return self._response(201, item, request_id, correlation_id)
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_tasks(context, query)
            return self._response(200, page, request_id, correlation_id)

        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            task_id, command = segments[1].split(":", 1)
            if command == "queue":
                item = await self._control_plane.queue_task(context, task_id)
            elif command == "start":
                item = await self._control_plane.start_task(context, task_id)
            elif command == "cancel":
                item = await self._control_plane.cancel_task(context, task_id)
            elif command == "retry":
                item = await self._control_plane.retry_task(context, task_id)
            else:
                raise APIException(status=404, code="not_found", message="unknown task command")
            return self._response(200, item, request_id, correlation_id)

        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_task(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 3 and segments[2] == "runs" and request.method == "GET":
            page = await self._control_plane.list_runs(context, query, task_id=segments[1])
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 3 and segments[2] == "timeline" and request.method == "GET":
            page = await self._control_plane.timeline(context, segments[1], query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 4 and segments[2] == "runs" and request.method == "GET":
            item = await self._control_plane.get_run(context, segments[3], task_id=segments[1])
            return self._response(200, item, request_id, correlation_id)
        if (
            len(segments) == 4
            and segments[2] == "runs"
            and segments[3].endswith(":cancel")
            and request.method == "POST"
        ):
            run_id = segments[3].removesuffix(":cancel")
            item = await self._control_plane.cancel_run(context, segments[1], run_id)
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 4 and segments[2:] == ["events", "stream"]:
            raise APIException(
                status=406,
                code="stream_transport_required",
                message="use the SSE transport for this endpoint",
            )
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _runs(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_runs(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_run(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _model_providers(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_model_providers(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            provider_id, command = segments[1].rsplit(":", 1)
            if command == "enable":
                item = await self._control_plane.set_model_provider_enabled(
                    context, provider_id, enabled=True
                )
            elif command == "disable":
                item = await self._control_plane.set_model_provider_enabled(
                    context, provider_id, enabled=False
                )
            elif command == "refresh-health":
                item = await self._control_plane.refresh_model_provider_health(context, provider_id)
            else:
                raise APIException(
                    status=404,
                    code="not_found",
                    message="unknown model-provider command",
                )
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_model_provider(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _models(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_models(context, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            model_id, command = segments[1].rsplit(":", 1)
            if command == "enable":
                item = await self._control_plane.set_model_enabled(context, model_id, enabled=True)
            elif command == "disable":
                item = await self._control_plane.set_model_enabled(context, model_id, enabled=False)
            else:
                raise APIException(
                    status=404,
                    code="not_found",
                    message="unknown model command",
                )
            return self._response(200, item, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_model(context, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    async def _references(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        collection: Literal["plans", "steps", "artifacts", "results"],
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 1 and request.method == "GET":
            page = await self._control_plane.list_references(context, collection, query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 2 and request.method == "GET":
            item = await self._control_plane.get_reference(context, collection, segments[1])
            return self._response(200, item, request_id, correlation_id)
        raise APIException(status=405, code="method_not_allowed", message="method not allowed")

    @staticmethod
    def _response(
        status: int,
        body: JsonValue,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        return HTTPResponse(
            status=status,
            body=body,
            headers={
                "content-type": "application/json",
                "x-request-id": request_id,
                "x-correlation-id": correlation_id,
                "x-api-version": API_VERSION,
            },
        )

    @classmethod
    def _error_response(
        cls,
        error: APIException,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        payload = APIError(
            code=error.code,
            message=error.message,
            request_id=request_id,
            correlation_id=correlation_id,
            retryable=error.retryable,
            details=error.details,
        )
        return cls._response(error.status, payload.to_json(), request_id, correlation_id)


ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


class ControlPlaneASGI:
    """Small replaceable ASGI transport proving HTTP/SSE without framework lock-in."""

    def __init__(self, http: ControlPlaneHTTP) -> None:
        self._http = http

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http":
            raise RuntimeError("ControlPlaneASGI supports HTTP scopes only")
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        headers = _decode_asgi_headers(scope.get("headers", []))
        query = dict(
            parse_qsl(
                bytes(scope.get("query_string", b"")).decode("utf-8"),
                keep_blank_values=True,
            )
        )

        if method == "GET" and _is_event_stream_path(path):
            await self._stream_events(path, headers, query, send)
            return

        raw_body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if isinstance(chunk, bytes):
                raw_body.extend(chunk)
            if not message.get("more_body", False):
                break

        body: dict[str, JsonValue] = {}
        if raw_body:
            try:
                decoded = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._send_raw_error(
                    400,
                    "invalid_json",
                    "request body is not valid JSON",
                    headers,
                    send,
                )
                return
            if not isinstance(decoded, dict):
                await self._send_raw_error(
                    400,
                    "invalid_request",
                    "request JSON body must be an object",
                    headers,
                    send,
                )
                return
            body = decoded

        response = await self._http.handle(
            HTTPRequest(method=method, path=path, headers=headers, query=query, body=body)
        )
        await _send_response(response, send)

    async def _stream_events(
        self,
        path: str,
        headers: dict[str, str],
        query: dict[str, str],
        send: ASGISend,
    ) -> None:
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        started = False
        try:
            version, relative = _split_version(path)
            _require_supported_version(version)
            segments = [segment for segment in relative.split("/") if segment]
            if len(segments) != 4 or segments[0] != "tasks" or segments[2:] != ["events", "stream"]:
                raise APIException(status=404, code="not_found", message="route not found")
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
            stream = await self._http._control_plane.subscribe_task_events(
                context,
                segments[1],
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
                payload = json.dumps(event, separators=(",", ":"), default=str)
                await send(
                    {
                        "type": "http.response.body",
                        "body": f"event: platform.event\ndata: {payload}\n\n".encode(),
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

    async def _send_raw_error(
        self,
        status: int,
        code: str,
        message: str,
        headers: dict[str, str],
        send: ASGISend,
    ) -> None:
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        await _send_response(
            self._http._error_response(
                APIException(status=status, code=code, message=message),
                request_id,
                correlation_id,
            ),
            send,
        )


def _request_context(
    request: HTTPRequest,
    request_id: str,
    correlation_id: str,
) -> RequestContext:
    actor = request.trusted_actor
    if actor is None:
        owner_type_value = _header(request.headers, "x-owner-type")
        owner_type: OwnerType | None = None
        if owner_type_value is not None:
            if owner_type_value not in {"user", "organization", "team", "service"}:
                raise APIException(
                    status=400,
                    code="invalid_request",
                    message="X-Owner-Type is invalid",
                    details={"header": "X-Owner-Type"},
                )
            owner_type = cast(OwnerType, owner_type_value)
        actor = ActorContext(
            principal_ref=_header(request.headers, "x-principal-ref") or "local:anonymous",
            owner_type=owner_type,
            owner_id=_header(request.headers, "x-owner-id"),
        )
    return RequestContext(
        request_id=request_id,
        correlation_id=correlation_id,
        actor=actor,
        idempotency_key=_header(request.headers, "idempotency-key"),
    )


def _split_version(path: str) -> tuple[str, str]:
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "api":
        raise APIException(status=404, code="not_found", message="route not found")
    version = segments[1]
    relative = "/" + "/".join(segments[2:]) if len(segments) > 2 else ""
    return version, relative


def _require_supported_version(version: str) -> None:
    if version not in SUPPORTED_API_VERSIONS:
        raise APIException(
            status=400,
            code="unsupported_api_version",
            message=f"unsupported API version: {version}",
            details={"supported_versions": list(SUPPORTED_API_VERSIONS)},
        )


def _page_query(query: Mapping[str, str]) -> PageQuery:
    filters = {
        key[7:-1]: value
        for key, value in query.items()
        if key.startswith("filter[") and key.endswith("]")
    }
    try:
        limit = int(query.get("limit", "50"))
    except ValueError as exc:
        raise APIException(
            status=400,
            code="invalid_request",
            message="limit must be an integer",
            details={"field": "limit"},
        ) from exc
    raw_direction = query.get("direction", "asc")
    if raw_direction not in {"asc", "desc"}:
        raise APIException(
            status=400,
            code="invalid_request",
            message="direction must be asc or desc",
            details={"field": "direction"},
        )
    return PageQuery(
        limit=limit,
        cursor=query.get("cursor"),
        sort=query.get("sort", "id"),
        direction=cast(Literal["asc", "desc"], raw_direction),
        search=query.get("q"),
        filters=filters,
        fields=_csv(query.get("fields")),
    )


def _csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _require_json(request: HTTPRequest) -> None:
    content_type = (_header(request.headers, "content-type") or "").split(";", 1)[0].strip()
    if content_type != "application/json":
        raise APIException(
            status=415,
            code="unsupported_media_type",
            message="mutating resource requests require application/json",
            details={"expected": "application/json"},
        )


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    for key, value in headers.items():
        if key.casefold() == target:
            return value
    return None


def _decode_asgi_headers(raw_headers: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(raw_headers, list | tuple):
        return result
    for pair in raw_headers:
        if not isinstance(pair, list | tuple) or len(pair) != 2:
            continue
        raw_name, raw_value = pair
        if isinstance(raw_name, bytes) and isinstance(raw_value, bytes):
            result[raw_name.decode("latin-1").casefold()] = raw_value.decode("latin-1")
    return result


def _is_event_stream_path(path: str) -> bool:
    return path.endswith("/events/stream") and "/tasks/" in path


async def _send_response(response: HTTPResponse, send: ASGISend) -> None:
    body = json.dumps(response.body, separators=(",", ":"), default=str).encode()
    headers = [
        (name.encode("latin-1"), value.encode("latin-1"))
        for name, value in response.headers.items()
    ]
    await send({"type": "http.response.start", "status": response.status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_sse_error(
    error: APIException,
    request_id: str,
    correlation_id: str,
    send: ASGISend,
) -> None:
    payload = APIError(
        code=error.code,
        message=error.message,
        request_id=request_id,
        correlation_id=correlation_id,
        retryable=error.retryable,
        details=error.details,
    ).to_json()
    data = json.dumps(payload, separators=(",", ":"), default=str)
    await send(
        {
            "type": "http.response.body",
            "body": f"event: platform.error\ndata: {data}\n\n".encode(),
            "more_body": False,
        }
    )
