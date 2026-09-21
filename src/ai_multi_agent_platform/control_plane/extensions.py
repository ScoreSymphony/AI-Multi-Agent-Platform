"""Registration-based Control Plane extensions for later platform domains.

The Control Plane owns the stable Task/Run foundation. Canonical domains implemented by
later issues extend that foundation only when their contracts exist. Generic future
domains are therefore registered explicitly instead of being predeclared here.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
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

# These collections are implemented by later completed model-domain work. They are
# part of the current composed API, but not part of the Task/Run foundation itself.
IMPLEMENTED_DOMAIN_COLLECTIONS = (
    "model-providers",
    "models",
)

PLATFORM_COLLECTIONS = FOUNDATION_COLLECTIONS + IMPLEMENTED_DOMAIN_COLLECTIONS
BASE_COLLECTIONS = frozenset(PLATFORM_COLLECTIONS)

# Kept as a compatibility export. the foundation no longer predeclares commands owned by future
# domains; later domains register their own commands explicitly.
REQUIRED_COMMANDS: tuple[str, ...] = ()

_RESERVED_COLLECTIONS = BASE_COLLECTIONS | {"timeline", "commands"}
_COLLECTION_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_MODULE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)*$")


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


class CommandAuthorizer(Protocol):
    """Explicit replacement for the generic command authorization preflight.

    Most modules should omit this and inherit the canonical
    ``_authorize(context, command, resource_ref)`` policy. A domain whose existing
    contract authorizes a richer relationship (for example source + destination
    scopes) can declare that policy explicitly without retaining an ``execute_command``
    subclass override solely to control MRO dispatch.
    """

    def __call__(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> Awaitable[None]: ...


class CommandObserver(Protocol):
    """Post-success projection hook owned by an explicitly registered module."""

    def __call__(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        result: dict[str, JsonValue],
    ) -> Awaitable[None]: ...


RouteHandler = Callable[[HTTPRequest], Awaitable[HTTPResponse]]
OpenAPIContributor = Callable[[dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class ControlPlaneRoute:
    """One exact HTTP route explicitly owned by a Control Plane module."""

    method: str
    path: str
    handler: RouteHandler

    def __post_init__(self) -> None:
        method = self.method.upper().strip()
        path = _normalize_route_path(self.path)
        if not method:
            raise ValueError("Control Plane route method must be non-blank")
        object.__setattr__(self, "method", method)
        object.__setattr__(self, "path", path)


@dataclass(frozen=True, slots=True)
class ControlPlaneModule:
    """Explicit northbound contribution owned by one platform domain.

    Domain services remain the behavior owners. A module only declares how those
    services are exposed through the canonical Control Plane. Module batches are
    validated before installation, making conflicts deterministic and independent
    of registration order.

    ``command_authorizers`` is intentionally narrow: it can customize authorization
    only for commands owned by the same module. ``command_observers`` run after a
    successful, privacy-validated command result and are intended for derived audit or
    projection side effects, not for command ownership or dispatch.

    ``discover_as_extension`` separates explicit runtime ownership from the legacy
    extension-discovery contract. Canonical modules that have dedicated API metadata
    may remain fully owned/dispatchable without being advertised as generic extensions.
    """

    name: str
    resource_services: Mapping[str, ResourceService] = field(default_factory=dict)
    command_handlers: Mapping[str, CommandHandler] = field(default_factory=dict)
    command_authorizers: Mapping[str, CommandAuthorizer] = field(default_factory=dict)
    command_observers: tuple[CommandObserver, ...] = ()
    routes: tuple[ControlPlaneRoute, ...] = ()
    openapi_contributors: tuple[OpenAPIContributor, ...] = ()
    requires: frozenset[str] = frozenset()
    discover_as_extension: bool = True

    def __post_init__(self) -> None:
        if _MODULE_PATTERN.fullmatch(self.name) is None:
            raise ValueError("Control Plane module name must use lowercase canonical segments")
        if self.name in self.requires:
            raise ValueError("Control Plane module cannot require itself")
        orphan_authorizers = sorted(set(self.command_authorizers).difference(self.command_handlers))
        if orphan_authorizers:
            raise ValueError(
                "Control Plane module command authorizers must belong to commands "
                f"owned by the same module: {orphan_authorizers!r}"
            )


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
    """Task/Run foundation plus explicitly registered later-domain extensions."""

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
        modules: Sequence[ControlPlaneModule] = (),
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
        self._resource_owners: dict[str, str] = {}
        self._command_owners: dict[str, str] = {}
        self._command_authorizers: dict[str, CommandAuthorizer] = {}
        self._command_observers: list[tuple[str, CommandObserver]] = []
        self._route_handlers: dict[tuple[str, str], RouteHandler] = {}
        self._route_owners: dict[tuple[str, str], str] = {}
        self._openapi_contributors: list[tuple[str, OpenAPIContributor]] = []
        self._registered_modules: dict[str, ControlPlaneModule] = {}
        for collection, service in (resource_services or {}).items():
            ControlPlane.register_resource_service(
                self,
                collection,
                service,
                owner="constructor",
            )
        for command, handler in (command_handlers or {}).items():
            ControlPlane.register_command(
                self,
                command,
                handler,
                owner="constructor",
            )
        self.register_modules(modules)

    @property
    def registered_collections(self) -> tuple[str, ...]:
        return tuple(sorted(self._resource_services))

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return tuple(sorted(self._command_handlers))

    @property
    def extension_collections(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                collection
                for collection, owner in self._resource_owners.items()
                if self._owner_is_extension_discoverable(owner)
            )
        )

    @property
    def extension_commands(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                command
                for command, owner in self._command_owners.items()
                if self._owner_is_extension_discoverable(owner)
            )
        )

    @property
    def registered_modules(self) -> tuple[str, ...]:
        return tuple(sorted(self._registered_modules))

    @property
    def registered_routes(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._route_handlers))

    def resource_owner(self, collection: str) -> str | None:
        return self._resource_owners.get(collection)

    def command_owner(self, command: str) -> str | None:
        return self._command_owners.get(command)

    def route_owner(self, method: str, path: str) -> str | None:
        return self._route_owners.get(_route_key(method, path))

    def _owner_is_extension_discoverable(self, owner: str) -> bool:
        if owner in {"constructor", "manual"}:
            return True
        module = self._registered_modules.get(owner)
        return module is None or module.discover_as_extension

    def register_resource_service(
        self,
        collection: str,
        service: ResourceService,
        *,
        owner: str = "manual",
    ) -> None:
        _validate_extension_collection(collection)
        _validate_owner(owner)
        existing_owner = self._resource_owners.get(collection)
        if existing_owner is not None:
            raise ValueError(
                "duplicate Control Plane resource ownership for "
                f"{collection!r}: {existing_owner!r} and {owner!r}"
            )
        self._resource_services[collection] = service
        self._resource_owners[collection] = owner

    def register_command(
        self,
        command: str,
        handler: CommandHandler,
        *,
        owner: str = "manual",
    ) -> None:
        _validate_command_name(command)
        _validate_owner(owner)
        existing_owner = self._command_owners.get(command)
        if existing_owner is not None:
            raise ValueError(
                "duplicate Control Plane command ownership for "
                f"{command!r}: {existing_owner!r} and {owner!r}"
            )
        self._command_handlers[command] = handler
        self._command_owners[command] = owner

    def register_modules(self, modules: Sequence[ControlPlaneModule]) -> None:
        """Validate and install a module batch without registration-order semantics."""

        if not modules:
            return
        by_name: dict[str, ControlPlaneModule] = {}
        for module in modules:
            if module.name in by_name or module.name in self._registered_modules:
                raise ValueError(f"duplicate Control Plane module ownership: {module.name!r}")
            by_name[module.name] = module

        available = set(self._registered_modules) | set(by_name)
        for module in by_name.values():
            missing = sorted(module.requires.difference(available))
            if missing:
                raise ValueError(
                    f"Control Plane module {module.name!r} requires missing modules: {missing!r}"
                )

        resource_claims = dict(self._resource_owners)
        command_claims = dict(self._command_owners)
        route_claims = dict(self._route_owners)
        for name in sorted(by_name):
            module = by_name[name]
            for collection in sorted(module.resource_services):
                _validate_extension_collection(collection)
                _claim(resource_claims, collection, name, kind="resource")
            for command in sorted(module.command_handlers):
                _validate_command_name(command)
                _claim(command_claims, command, name, kind="command")
            for route in module.routes:
                _claim(route_claims, _route_key(route.method, route.path), name, kind="route")

        # Commit only after all claims have been validated. Explicit base-class
        # dispatch prevents a legacy compatibility subclass from turning module
        # installation back into MRO-sensitive behavior during the migration.
        installation_order = _module_dependency_order(by_name)
        for name in installation_order:
            module = by_name[name]
            for collection, service in sorted(module.resource_services.items()):
                ControlPlane.register_resource_service(
                    self,
                    collection,
                    service,
                    owner=name,
                )
            for command, handler in sorted(module.command_handlers.items()):
                ControlPlane.register_command(
                    self,
                    command,
                    handler,
                    owner=name,
                )
            for command, authorizer in sorted(module.command_authorizers.items()):
                self._command_authorizers[command] = authorizer
            for observer in module.command_observers:
                self._command_observers.append((name, observer))
            for route in sorted(module.routes, key=lambda item: (item.method, item.path)):
                key = _route_key(route.method, route.path)
                self._route_handlers[key] = route.handler
                self._route_owners[key] = name
            for contributor in module.openapi_contributors:
                self._openapi_contributors.append((name, contributor))
            self._registered_modules[name] = module
        dependency_order = {
            name: index
            for index, name in enumerate(_module_dependency_order(self._registered_modules))
        }
        self._command_observers.sort(key=lambda item: dependency_order[item[0]])
        self._openapi_contributors.sort(key=lambda item: dependency_order[item[0]])

    def apply_openapi_contributions(self, specification: dict[str, Any]) -> dict[str, Any]:
        for _, contributor in self._openapi_contributors:
            contributor(specification)
        return specification

    async def dispatch_registered_route(self, request: HTTPRequest) -> HTTPResponse | None:
        handler = self._route_handlers.get(_route_key(request.method, request.path))
        if handler is None:
            return None
        return await handler(request)

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
        effective_payload = payload or {}
        authorizer = self._command_authorizers.get(command)
        if authorizer is None:
            await self._authorize(context, command, resource_ref)
        else:
            await authorizer(context, resource_ref, effective_payload)
        result = await handler(context, resource_ref, effective_payload)
        _reject_private_payload(result)
        for _, observer in self._command_observers:
            await observer(context, command, resource_ref, result)
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
            registered_collections = self._extended_control_plane.registered_collections
            registered_commands = self._extended_control_plane.registered_commands
            extension_collections = getattr(
                self._extended_control_plane,
                "extension_collections",
                registered_collections,
            )
            extension_commands = getattr(
                self._extended_control_plane,
                "extension_commands",
                registered_commands,
            )

            if request.method == "GET" and relative == "/openapi.json":
                specification = build_openapi(
                    extension_collections=extension_collections,
                    extension_commands=extension_commands,
                )
                contributor = getattr(
                    self._extended_control_plane,
                    "apply_openapi_contributions",
                    None,
                )
                if callable(contributor):
                    specification = contributor(specification)
                return self._response(200, specification, request_id, correlation_id)

            if request.method == "GET" and relative in {"", "/"}:
                manifest_resources: list[JsonValue] = [
                    *PLATFORM_COLLECTIONS,
                    *extension_collections,
                    "timeline",
                ]
                manifest_commands: list[JsonValue] = [command for command in extension_commands]
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

            route_response: HTTPResponse | None = None
            route_dispatcher = getattr(
                self._extended_control_plane,
                "dispatch_registered_route",
                None,
            )
            if callable(route_dispatcher):
                route_response = await route_dispatcher(request)
            if route_response is not None:
                headers = dict(route_response.headers)
                headers.setdefault("X-Request-Id", request_id)
                headers.setdefault("X-Correlation-Id", correlation_id)
                return HTTPResponse(
                    status=route_response.status,
                    body=route_response.body,
                    headers=headers,
                )

            segments = [segment for segment in relative.split("/") if segment]
            if segments and segments[0] in registered_collections:
                context = _request_context(request, request_id, correlation_id)
                if len(segments) == 1:
                    if request.method != "GET":
                        raise APIException(
                            status=405,
                            code="method_not_allowed",
                            message="method not allowed",
                        )
                    page = await self._extended_control_plane.list_extension_resources(
                        context,
                        segments[0],
                        _page_query(request.query),
                    )
                    return self._response(200, page, request_id, correlation_id)
                if len(segments) == 2:
                    if request.method != "GET":
                        raise APIException(
                            status=405,
                            code="method_not_allowed",
                            message="method not allowed",
                        )
                    item = await self._extended_control_plane.get_extension_resource(
                        context,
                        segments[0],
                        segments[1],
                    )
                    return self._response(200, item, request_id, correlation_id)
                normalized_path = request.path.rstrip("/") or "/"
                registered_routes = getattr(
                    self._extended_control_plane,
                    "registered_routes",
                    (),
                )
                if not any(route_path == normalized_path for _, route_path in registered_routes):
                    raise APIException(status=404, code="not_found", message="route not found")

            if (
                segments
                and segments[0] == "commands"
                and len(segments) == 2
                and registered_commands
            ):
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

        base_response = await super().handle(request)
        is_route_not_found = (
            base_response.status == 404
            and isinstance(base_response.body, dict)
            and base_response.body.get("code") == "not_found"
            and base_response.body.get("message") == "route not found"
        )
        if is_route_not_found:
            normalized_path = request.path.rstrip("/") or "/"
            registered_routes = getattr(
                self._extended_control_plane,
                "registered_routes",
                (),
            )
            if any(route_path == normalized_path for _, route_path in registered_routes):
                return self._error_response(
                    APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    ),
                    request_id,
                    correlation_id,
                )
        return base_response


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


def _validate_owner(owner: str) -> None:
    if not owner.strip():
        raise ValueError("Control Plane ownership label must be non-blank")


def _module_dependency_order(
    modules: Mapping[str, ControlPlaneModule],
) -> tuple[str, ...]:
    """Return deterministic dependency-first module order or reject a cycle."""

    remaining = set(modules)
    ordered: list[str] = []
    while remaining:
        ready = sorted(name for name in remaining if modules[name].requires.isdisjoint(remaining))
        if not ready:
            raise ValueError(f"Control Plane module dependency cycle: {sorted(remaining)!r}")
        ordered.extend(ready)
        remaining.difference_update(ready)
    return tuple(ordered)


def _claim(
    claims: dict[Any, str],
    key: Any,
    owner: str,
    *,
    kind: str,
) -> None:
    existing_owner = claims.get(key)
    if existing_owner is not None:
        raise ValueError(
            f"duplicate Control Plane {kind} ownership for {key!r}: "
            f"{existing_owner!r} and {owner!r}"
        )
    claims[key] = owner


def _normalize_route_path(path: str) -> str:
    if not path.startswith("/"):
        raise ValueError("Control Plane route path must be absolute")
    return "/" + "/".join(segment for segment in path.split("/") if segment)


def _route_key(method: str, path: str) -> tuple[str, str]:
    normalized_method = method.upper().strip()
    normalized_path = _normalize_route_path(path)
    if not normalized_method:
        raise ValueError("Control Plane route method must be non-blank")
    return normalized_method, normalized_path


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
