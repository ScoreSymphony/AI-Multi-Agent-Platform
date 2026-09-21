"""Read-only release/update status on the current Control Plane surface."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import ControlPlaneModule, ControlPlaneRoute
from .http import (
    ASGIReceive,
    ASGISend,
    HTTPRequest,
    HTTPResponse,
    _decode_asgi_headers,
    _header,
    _send_response,
    _send_sse_error,
)
from .models import API_VERSION, APIException
from .openapi_errors import ensure_method_not_allowed_responses
from .module_registry import install_control_plane_modules
from .northbound_errors import api_exception_for_boundary, log_unexpected_boundary_error
from .task_project_reassignment import (
    AuthenticatedControlPlaneHTTP as _CurrentAuthenticatedControlPlaneHTTP,
)
from .task_project_reassignment import ControlPlane
from .task_project_reassignment import ControlPlaneASGI as _CurrentControlPlaneASGI
from .task_project_reassignment import ControlPlaneHTTP as _CurrentControlPlaneHTTP
from .task_project_reassignment import build_openapi as _build_current_openapi
from .transport_contract_schemas import augment_transport_schemas
from .transport_request_schemas import augment_transport_request_schemas
from .transport_schema_composition import preserve_existing_transport_schemas

if TYPE_CHECKING:
    from ai_multi_agent_platform.release.operator import ReleaseOperatorService

RELEASE_STATUS_PATH = f"/api/{API_VERSION}/release/status"
RELEASE_STATUS_MODULE = "release-status"


class _HTTPDisconnect(Exception):
    """Private control-flow signal for a client that has left before a response exists."""


def _runtime_release_operator() -> ReleaseOperatorService:
    from ai_multi_agent_platform.release.operator import ReleaseOperatorService

    return ReleaseOperatorService.runtime_defaults()


def _release_status_module(operator: ReleaseOperatorService) -> ControlPlaneModule:
    async def release_status(request: HTTPRequest) -> HTTPResponse:
        del request
        return HTTPResponse(
            status=200,
            body=cast(JsonValue, operator.status()),
            headers={},
        )

    return ControlPlaneModule(
        name=RELEASE_STATUS_MODULE,
        routes=(
            ControlPlaneRoute(
                method="GET",
                path=RELEASE_STATUS_PATH,
                handler=release_status,
            ),
        ),
        openapi_contributors=(_augment_openapi,),
    )


def _install_release_status_module(
    control_plane: Any,
    release_operator: ReleaseOperatorService | None,
) -> ReleaseOperatorService:
    existing = getattr(control_plane, "_release_operator", None)
    if existing is not None:
        if release_operator is not None and release_operator is not existing:
            raise ValueError("release status operator is already bound to this Control Plane")
        return cast("ReleaseOperatorService", existing)

    operator = release_operator or _runtime_release_operator()
    registered_modules = getattr(control_plane, "registered_modules", ())
    if RELEASE_STATUS_MODULE not in registered_modules and hasattr(
        control_plane, "register_modules"
    ):
        install_control_plane_modules(control_plane, (_release_status_module(operator),))
    control_plane._release_operator = operator
    return operator


def _augment_root_manifest(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    manifest = deepcopy(body)
    manifest["release_status"] = RELEASE_STATUS_PATH
    return manifest


def _filter_extension_discovery(
    control_plane: Any,
    body: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """Keep legacy extension metadata separate from explicit canonical ownership."""

    specification = deepcopy(body)
    for key, attribute in (
        ("x-registered-extension-collections", "extension_collections"),
        ("x-registered-extension-commands", "extension_commands"),
    ):
        raw = specification.get(key)
        if not isinstance(raw, list):
            continue
        existing = tuple(value for value in raw if isinstance(value, str))
        discoverable = getattr(control_plane, attribute, existing)
        allowed = set(discoverable) if isinstance(discoverable, tuple) else set(existing)
        specification[key] = [value for value in existing if value in allowed]
    return specification


def _baseline_schemas(specification: dict[str, Any]) -> dict[str, Any]:
    components = specification.get("components")
    if not isinstance(components, dict):
        return {}
    schemas = components.get("schemas")
    if not isinstance(schemas, dict):
        return {}
    return deepcopy(schemas)


def _augment_generated_transport_contract(specification: dict[str, Any]) -> dict[str, Any]:
    baseline = _baseline_schemas(specification)
    augment_transport_schemas(specification)
    augment_transport_request_schemas(specification)
    return preserve_existing_transport_schemas(specification, baseline)


class ControlPlaneHTTP(_CurrentControlPlaneHTTP):
    """Expose release metadata through an explicitly owned special route.

    This is also the current public HTTP façade, so it owns the final ordinary
    ``Exception`` containment point. Process-control signals derive from
    ``BaseException`` and therefore continue to propagate.
    """

    def __init__(
        self,
        control_plane: Any,
        *,
        release_operator: ReleaseOperatorService | None = None,
    ) -> None:
        super().__init__(control_plane)
        self._release_operator = _install_release_status_module(
            control_plane,
            release_operator,
        )

    @property
    def release_operator(self) -> ReleaseOperatorService:
        return self._release_operator

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        boundary_headers = dict(request.headers)
        boundary_headers["x-request-id"] = request_id
        boundary_headers["x-correlation-id"] = correlation_id
        boundary_request = HTTPRequest(
            method=request.method,
            path=request.path,
            headers=boundary_headers,
            query=request.query,
            body=request.body,
            trusted_actor=request.trusted_actor,
        )
        try:
            response = await super().handle(boundary_request)
            if response.status != 200 or not isinstance(response.body, dict):
                return response
            if request.method != "GET":
                return response
            normalized_path = request.path.rstrip("/")
            if normalized_path == f"/api/{API_VERSION}":
                return HTTPResponse(
                    status=response.status,
                    body=_augment_root_manifest(response.body),
                    headers=dict(response.headers),
                )
            if normalized_path == f"/api/{API_VERSION}/openapi.json":
                specification = _filter_extension_discovery(self._control_plane, response.body)
                _augment_generated_transport_contract(cast(dict[str, Any], specification))
                return HTTPResponse(
                    status=response.status,
                    body=specification,
                    headers=dict(response.headers),
                )
            return response
        # error-boundary: allow-broad-catch=boundary public Control Plane HTTP containment
        except Exception as exc:
            if not isinstance(exc, (ContractError, APIException)):
                log_unexpected_boundary_error(
                    exc,
                    boundary="control-plane-http",
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            return self._error_response(
                api_exception_for_boundary(exc),
                request_id,
                correlation_id,
            )


class AuthenticatedControlPlaneHTTP(_CurrentAuthenticatedControlPlaneHTTP):
    """Authenticate normally, then expose the explicitly owned #42 status route."""

    def __init__(
        self,
        *args: Any,
        release_operator: ReleaseOperatorService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        release_status_http = ControlPlaneHTTP(
            self._control_plane,
            release_operator=release_operator,
        )
        self._release_status_http = release_status_http
        self._current_http = release_status_http

    @property
    def release_operator(self) -> ReleaseOperatorService:
        return self._release_status_http.release_operator


class ControlPlaneASGI:
    """Outermost public ASGI containment around the current composed application.

    Ordinary HTTP errors are returned through the same canonical envelope as direct
    ``ControlPlaneHTTP`` calls. SSE failures that occur after response start are
    terminated with the canonical ``platform.error`` event. A failed ASGI ``send``
    is never retried as an API error, and client disconnect is not converted to 5xx.
    """

    def __init__(self, http: Any) -> None:
        self._http = http
        self._inner = _CurrentControlPlaneASGI(http)

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if scope.get("type") != "http":
            await self._inner(scope, receive, send)
            return

        headers = _decode_asgi_headers(scope.get("headers", []))
        request_id = headers.get("x-request-id") or f"request_{uuid4()}"
        correlation_id = headers.get("x-correlation-id") or request_id
        started = False
        event_stream = False
        send_failed = False

        async def disconnect_aware_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "http.disconnect":
                raise _HTTPDisconnect
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal started, event_stream, send_failed
            try:
                await send(message)
            # error-boundary: allow-broad-catch=boundary ASGI send passthrough
            except Exception:
                send_failed = True
                raise
            if message.get("type") != "http.response.start":
                return
            started = True
            response_headers = _decode_asgi_headers(message.get("headers", []))
            event_stream = response_headers.get("content-type", "").startswith("text/event-stream")

        try:
            await self._inner(scope, disconnect_aware_receive, tracked_send)
        except _HTTPDisconnect:
            return
        # error-boundary: allow-broad-catch=boundary public ASGI containment
        except Exception as exc:
            if send_failed:
                raise
            if not isinstance(exc, (ContractError, APIException)):
                log_unexpected_boundary_error(
                    exc,
                    boundary="control-plane-asgi",
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            error = api_exception_for_boundary(exc)
            if started:
                if event_stream:
                    await _send_sse_error(error, request_id, correlation_id, send)
                    return
                raise
            await _send_response(
                ControlPlaneHTTP._error_response(error, request_id, correlation_id),
                send,
            )


def _augment_openapi(specification: dict[str, Any]) -> None:
    paths = specification.get("paths")
    if isinstance(paths, dict):
        paths[RELEASE_STATUS_PATH] = {
            "get": {
                "operationId": "getReleaseStatus",
                "summary": "Read platform release, compatibility and upstream update status",
                "responses": {
                    "200": {"description": "Read-only release/update operator status"},
                    "401": {"description": "Authentication required"},
                },
            }
        }
    specification["x-release-update-policy"] = {
        "discovery": "advisory-only",
        "automatic_production_updates": False,
        "production_pin_mutation": "not_exposed_by_control_plane",
    }


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
    include_approval_decisions: bool = False,
) -> dict[str, Any]:
    specification = _build_current_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
        include_conversations=include_conversations,
        include_approval_decisions=include_approval_decisions,
    )
    _augment_openapi(specification)
    _augment_generated_transport_contract(specification)
    return ensure_method_not_allowed_responses(specification)


__all__ = [
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "RELEASE_STATUS_MODULE",
    "RELEASE_STATUS_PATH",
    "build_openapi",
]
