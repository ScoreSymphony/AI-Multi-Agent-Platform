"""Registration-based Control Plane extensions for later platform domains.

Issue #32 owns the stable Task/Run foundation. Canonical domains implemented by
later issues extend that foundation only when their contracts exist. Generic future
domains are therefore registered explicitly instead of being predeclared here.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Mapping, Sequence
from copy import deepcopy
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry

from .http import ControlPlaneHTTP as BaseControlPlaneHTTP
from .http import (
    HTTPRequest,
    HTTPResponse,
    _header,
    _page_query,
    _request_context,
    _require_json,
    _require_supported_version,
    _split_version,
)
from .models import (
    API_VERSION,
    APIException,
    PageQuery,
    RequestContext,
    api_exception_from_contract,
    paginate,
)
from .openapi import build_openapi as build_base_openapi
from .service import ControlPlane as BaseControlPlane
from .service import ScopeStore

FOUNDATION_COLLECTIONS = (
    "projects",
    "workspaces",
    "tasks",
    "plans",
    "steps",
    "runs",
    "artifacts",
    "results",
)

# These collections are implemented by later completed domain work (#10). They are
# part of the current composed API, but not part of the issue #32 foundation itself.
IMPLEMENTED_DOMAIN_COLLECTIONS = (
    "model-providers",
    "models",
)

PLATFORM_COLLECTIONS = FOUNDATION_COLLECTIONS + IMPLEMENTED_DOMAIN_COLLECTIONS
BASE_COLLECTIONS = frozenset(PLATFORM_COLLECTIONS)

# Kept as a compatibility export. #32 no longer predeclares commands owned by future
# domains; later domains register their own commands explicitly.
REQUIRED_COMMANDS: tuple[str, ...] = ()

_RESERVED_COLLECTIONS = BASE_COLLECTIONS | {"timeline", "commands"}
_COLLECTION_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")


@runtime_checkable
class ResourceService(Protocol):
    """Platform-owned read boundary for one explicitly registered collection."""

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]: ...

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]: ...


class CommandHandler(Protocol):
    """Explicit command seam registered by the canonical domain that owns it."""

    def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> Awaitable[dict[str, JsonValue]]: ...


class InMemoryResourceService:
    """Deterministic platform-owned service useful for composition and contract tests."""

    def __init__(self, resources: tuple[dict[str, JsonValue], ...] = ()) -> None:
        self._resources: dict[str, dict[str, JsonValue]] = {}
        for resource in resources:
            self.upsert(resource)

    def upsert(self, resource: dict[str, JsonValue]) -> None:
        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("resource requires a non-blank string id")
        self._resources[resource_id] = dict(resource)

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(dict(resource) for resource in self._resources.values())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            return dict(self._resources[resource_id])
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"resource not found: {resource_id}") from exc


class ControlPlane(BaseControlPlane):
    """Issue #32 foundation plus explicitly registered later-domain extensions."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        scopes: ScopeStore | None = None,
        authorization: AuthorizationProvider | None = None,
        live_events: EventProvider | None = None,
        health_providers: tuple[ProviderContract, ...] = (),
        model_registry: ModelRegistry | None = None,
        resource_services: Mapping[str, ResourceService] | None = None,
        command_handlers: Mapping[str, CommandHandler] | None = None,
    ) -> None:
        super().__init__(
            kernel=kernel,
            events=events,
            scopes=scopes,
            authorization=authorization,
            live_events=live_events,
            health_providers=health_providers,
            model_registry=model_registry,
        )
        self._resource_services: dict[str, ResourceService] = {}
        self._command_handlers: dict[str, CommandHandler] = {}
        for collection, service in (resource_services or {}).items():
            self.register_resource_service(collection, service)
        for command, handler in (command_handlers or {}).items():
            self.register_command(command, handler)

    @property
    def registered_collections(self) -> tuple[str, ...]:
        return tuple(sorted(self._resource_services))

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(sorted(self._command_handlers))

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        _validate_extension_collection(collection)
        self._resource_services[collection] = service

    def register_command(self, command: str, handler: CommandHandler) -> None:
        _validate_command_name(command)
        self._command_handlers[command] = handler

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        await self._authorize(context, f"{_singular(collection)}:list", collection)
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)
        return paginate(resources, query)

    async def get_extension_resource(
        self,
        context: RequestContext,
        collection: str,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        await self._authorize(context, f"{_singular(collection)}:read", resource_id)
        resource = await service.get_resource(context, resource_id)
        _validate_resources(collection, [resource])
        return resource

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        _validate_command_name(command)
        handler = self._command_handlers.get(command)
        if handler is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"canonical command is not registered: {command}",
                details={"command": command},
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        await self._authorize(context, command, resource_ref)
        result = await handler(context, resource_ref, payload or {})
        _reject_private_payload(result)
        return result

    def _registered_resource_service(self, collection: str) -> ResourceService:
        _validate_extension_collection(collection)
        service = self._resource_services.get(collection)
        if service is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"canonical collection is not registered: {collection}",
                details={"collection": collection},
            )
        return service


class ControlPlaneHTTP(BaseControlPlaneHTTP):
    """HTTP mapping for the current API plus explicit extension registrations."""

    def __init__(self, control_plane: ControlPlane) -> None:
        super().__init__(control_plane)
        self._extended_control_plane = control_plane

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            version, relative = _split_version(request.path)
            _require_supported_version(version)

            if request.method == "GET" and relative == "/openapi.json":
                specification = build_openapi(
                    extension_collections=self._extended_control_plane.registered_collections,
                    extension_commands=self._extended_control_plane.registered_commands,
                )
                return self._response(200, specification, request_id, correlation_id)

            if request.method == "GET" and relative in {"", "/"}:
                manifest_resources: list[JsonValue] = [
                    *PLATFORM_COLLECTIONS,
                    *self._extended_control_plane.registered_collections,
                    "timeline",
                ]
                manifest_commands: list[JsonValue] = [
                    command for command in self._extended_control_plane.registered_commands
                ]
                return self._response(
                    200,
                    {
                        "api_version": API_VERSION,
                        "resources": manifest_resources,
                        "commands": manifest_commands,
                        "openapi": f"/api/{API_VERSION}/openapi.json",
                        "live_updates": "sse",
                    },
                    request_id,
                    correlation_id,
                )

            segments = [segment for segment in relative.split("/") if segment]
            registered_collections = self._extended_control_plane.registered_collections
            if segments and segments[0] in registered_collections:
                context = _request_context(request, request_id, correlation_id)
                query = _page_query(request.query)
                if len(segments) == 1 and request.method == "GET":
                    page = await self._extended_control_plane.list_extension_resources(
                        context,
                        segments[0],
                        query,
                    )
                    return self._response(200, page, request_id, correlation_id)
                if len(segments) == 2 and request.method == "GET":
                    item = await self._extended_control_plane.get_extension_resource(
                        context,
                        segments[0],
                        segments[1],
                    )
                    return self._response(200, item, request_id, correlation_id)

            if segments and segments[0] == "commands" and len(segments) == 2:
                if request.method != "POST":
                    raise APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    )
                _require_json(request)
                context = _request_context(request, request_id, correlation_id)
                generic_resource_ref = request.body.get("resource_ref")
                if not isinstance(generic_resource_ref, str) or not generic_resource_ref.strip():
                    raise APIException(
                        status=400,
                        code="invalid_request",
                        message="resource_ref must be a non-blank string",
                        details={"field": "resource_ref"},
                    )
                payload = dict(request.body)
                payload.pop("resource_ref", None)
                item = await self._extended_control_plane.execute_command(
                    context,
                    segments[1],
                    generic_resource_ref,
                    payload,
                )
                return self._response(200, item, request_id, correlation_id)
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

        return await super().handle(request)


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Generate current OpenAPI plus only explicitly registered future extensions."""

    normalized_collections = tuple(sorted(set(extension_collections)))
    normalized_commands = tuple(sorted(set(extension_commands)))
    for collection in normalized_collections:
        _validate_extension_collection(collection)
    for command in normalized_commands:
        _validate_command_name(command)

    specification = deepcopy(build_base_openapi())
    paths = specification["paths"]
    assert isinstance(paths, dict)

    for collection in normalized_collections:
        paths[f"/api/{API_VERSION}/{collection}"] = {
            "get": _list_operation(f"list{_pascal(collection)}", f"Canonical {collection} page")
        }
        paths[f"/api/{API_VERSION}/{collection}/{{resource_id}}"] = {
            "get": _read_operation(
                f"get{_pascal(collection)}Resource",
                f"Canonical {_singular(collection)} resource",
            )
        }

    if normalized_commands:
        components = specification.get("components")
        assert isinstance(components, dict)
        schemas = components.get("schemas")
        assert isinstance(schemas, dict)
        schemas["CanonicalExtensionCommandRequest"] = {
            "type": "object",
            "required": ["resource_ref"],
            "properties": {
                "resource_ref": {"type": "string", "minLength": 1},
            },
            "additionalProperties": True,
        }
        paths[f"/api/{API_VERSION}/commands/{{command}}"] = {
            "post": _command_operation("executeCanonicalExtensionCommand", normalized_commands)
        }

    specification["x-control-plane-foundation-collections"] = list(FOUNDATION_COLLECTIONS)
    specification["x-implemented-domain-collections"] = list(IMPLEMENTED_DOMAIN_COLLECTIONS)
    specification["x-registered-extension-collections"] = list(normalized_collections)
    specification["x-registered-extension-commands"] = list(normalized_commands)
    return specification


def _list_operation(operation_id: str, description: str) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [
            {
                "name": "limit",
                "in": "query",
                "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
            },
            {"name": "cursor", "in": "query", "schema": {"type": "string"}},
            {"name": "sort", "in": "query", "schema": {"type": "string", "default": "id"}},
            {
                "name": "direction",
                "in": "query",
                "schema": {"type": "string", "enum": ["asc", "desc"]},
            },
            {"name": "q", "in": "query", "schema": {"type": "string"}},
            {"name": "fields", "in": "query", "schema": {"type": "string"}},
            {
                "name": "filter[field]",
                "in": "query",
                "description": (
                    "Exact canonical-field filter; replace field with a resource field name."
                ),
                "schema": {"type": "string"},
            },
        ],
        "responses": {"200": {"description": description}, **_error_responses()},
    }


def _read_operation(operation_id: str, description: str) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [
            {
                "name": "resource_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "responses": {"200": {"description": description}, **_error_responses()},
    }


def _command_operation(operation_id: str, commands: tuple[str, ...]) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "parameters": [
            {
                "name": "Idempotency-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "minLength": 1},
            },
            {
                "name": "command",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "enum": list(commands)},
            },
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/CanonicalExtensionCommandRequest"}
                }
            },
        },
        "responses": {
            "200": {"description": "Canonical extension command result"},
            **_error_responses(),
        },
    }


def _error_responses() -> dict[str, Any]:
    return {
        status: {"$ref": "#/components/responses/Error"}
        for status in (
            "400",
            "401",
            "403",
            "404",
            "409",
            "413",
            "415",
            "422",
            "429",
            "500",
            "502",
            "503",
            "504",
        )
    }


def _validate_extension_collection(collection: str) -> None:
    if collection in _RESERVED_COLLECTIONS:
        raise ValueError(f"extension collection conflicts with an existing route: {collection}")
    if _COLLECTION_PATTERN.fullmatch(collection) is None:
        raise ValueError("extension collection must use lowercase URL-safe names ([a-z][a-z0-9-]*)")


def _validate_command_name(command: str) -> None:
    if _COMMAND_PATTERN.fullmatch(command) is None:
        raise ValueError("command must use lowercase canonical segments separated by dots")


def _validate_resources(collection: str, resources: list[dict[str, JsonValue]]) -> None:
    for resource in resources:
        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"{collection} service returned a resource without a stable id",
            )
        _reject_private_payload(resource)


def _reject_private_payload(resource: Mapping[str, JsonValue]) -> None:
    forbidden = {
        "adapter_metadata",
        "backend_ref",
        "provider_sdk",
        "raw_exception",
        "private_api",
    }
    leaked: list[str] = []

    def inspect(value: object, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for raw_name, nested in value.items():
                name = str(raw_name)
                current_path = (*path, name)
                if name in forbidden:
                    leaked.append(".".join(current_path))
                inspect(nested, current_path)
            return
        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            for index, nested in enumerate(value):
                inspect(nested, (*path, str(index)))

    inspect(resource)
    if leaked:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"backend-private fields leaked through Control Plane: {sorted(leaked)!r}",
            details={"fields": cast(JsonValue, sorted(leaked))},
        )


def _singular(collection: str) -> str:
    irregular = {"memory": "memory", "knowledge": "knowledge", "capabilities": "capability"}
    if collection in irregular:
        return irregular[collection]
    if collection.endswith("ies"):
        return collection[:-3] + "y"
    if collection.endswith("s"):
        return collection[:-1]
    return collection


def _pascal(value: str) -> str:
    return "".join(part.capitalize() for part in value.replace("-", "_").split("_"))
