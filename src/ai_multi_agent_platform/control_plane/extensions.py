"""Extensible full-scope Control Plane surface required by issue #32.

The base Control Plane owns the resources whose application services already exist in
this repository. This module adds platform-owned registration seams for later domains
without proxying private backend APIs or inventing a second domain model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from copy import deepcopy
from typing import Any, Protocol, runtime_checkable
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

from .http import (
    ControlPlaneHTTP as BaseControlPlaneHTTP,
)
from .http import (
    HTTPRequest,
    HTTPResponse,
    _header,
    _page_query,
    _request_context,
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

PLATFORM_COLLECTIONS = (
    "projects",
    "workspaces",
    "tasks",
    "plans",
    "steps",
    "runs",
    "agents",
    "teams",
    "artifacts",
    "results",
    "files",
    "memory",
    "knowledge",
    "models",
    "providers",
    "tools",
    "capabilities",
    "nodes",
    "workers",
    "approvals",
    "automations",
    "evaluations",
    "plugins",
    "adapters",
)

BASE_COLLECTIONS = frozenset(
    {"projects", "workspaces", "tasks", "plans", "steps", "runs", "artifacts", "results"}
)
EXTENSION_COLLECTIONS = frozenset(PLATFORM_COLLECTIONS) - BASE_COLLECTIONS

REQUIRED_COMMANDS = (
    "approval.approve",
    "approval.deny",
    "adapter.enable",
    "adapter.disable",
    "plugin.enable",
    "plugin.disable",
    "worker.drain",
    "worker.restore",
    "automation.test",
    "evaluation.start",
)


@runtime_checkable
class ResourceService(Protocol):
    """Platform-owned read boundary for one canonical resource collection."""

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
    """Explicit command seam used by approvals, adapters, workers and automations."""

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
    """Full #32 northbound service boundary with replaceable extension services."""

    def __init__(
        self,
        *,
        kernel: PlatformKernel,
        events: EventRepository,
        scopes: ScopeStore | None = None,
        authorization: AuthorizationProvider | None = None,
        live_events: EventProvider | None = None,
        health_providers: tuple[ProviderContract, ...] = (),
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
        )
        self._resource_services = dict(resource_services or {})
        self._command_handlers = dict(command_handlers or {})
        for collection in self._resource_services:
            self._validate_extension_collection(collection)

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        self._validate_extension_collection(collection)
        self._resource_services[collection] = service

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command not in REQUIRED_COMMANDS:
            raise ValueError(f"unsupported canonical command: {command}")
        self._command_handlers[command] = handler

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        self._validate_extension_collection(collection)
        await self._authorize(context, f"{_singular(collection)}:list", collection)

        if collection in {"providers", "adapters", "plugins"}:
            resources = await self._provider_inventory(collection)
            return paginate(resources, query)
        if collection == "capabilities":
            resources = await self._capability_inventory()
            return paginate(resources, query)

        service = self._resource_services.get(collection)
        if service is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"canonical {collection} service is not configured",
                retryable=True,
                details={"collection": collection},
            )
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)
        return paginate(resources, query)

    async def get_extension_resource(
        self,
        context: RequestContext,
        collection: str,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        self._validate_extension_collection(collection)
        await self._authorize(context, f"{_singular(collection)}:read", resource_id)

        if collection in {"providers", "adapters", "plugins", "capabilities"}:
            page = await self.list_extension_resources(context, collection, PageQuery(limit=200))
            items = page.get("items")
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id") == resource_id:
                        return item
            raise ContractError(
                ErrorCode.NOT_FOUND, f"{collection} resource not found: {resource_id}"
            )

        service = self._resource_services.get(collection)
        if service is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"canonical {collection} service is not configured",
                retryable=True,
                details={"collection": collection},
            )
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
        if command not in REQUIRED_COMMANDS:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, f"unsupported canonical command: {command}"
            )
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        await self._authorize(context, command, resource_ref)
        handler = self._command_handlers.get(command)
        if handler is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"canonical command handler is not configured: {command}",
                retryable=True,
                details={"command": command},
            )
        result = await handler(context, resource_ref, payload or {})
        _reject_private_payload(result)
        return result

    async def _provider_inventory(self, collection: str) -> list[dict[str, JsonValue]]:
        resources: list[dict[str, JsonValue]] = []
        for provider in self._health_providers:
            descriptor = provider.descriptor
            health = await provider.health()
            resources.append(
                {
                    "id": descriptor.provider_id,
                    "type": _singular(collection),
                    "provider_type": descriptor.provider_type,
                    "contract_version": descriptor.contract_version,
                    "supported_operations": list(descriptor.supported_operations),
                    "health": health.value,
                    "available": descriptor.available,
                }
            )
        return resources

    async def _capability_inventory(self) -> list[dict[str, JsonValue]]:
        resources: list[dict[str, JsonValue]] = []
        for provider in self._health_providers:
            for capability in await provider.discover_capabilities():
                resources.append(
                    {
                        "id": f"{provider.descriptor.provider_id}:{capability.name}",
                        "type": "capability",
                        "provider_ref": provider.descriptor.provider_id,
                        "name": capability.name,
                        "kind": capability.kind.value,
                        "version": capability.version,
                        "supported_operations": list(capability.supported_operations),
                        "modalities": list(capability.modalities),
                        "features": list(capability.features),
                        "limits": dict(capability.limits),
                        "attributes": dict(capability.attributes),
                    }
                )
        return resources

    @staticmethod
    def _validate_extension_collection(collection: str) -> None:
        if collection not in EXTENSION_COLLECTIONS:
            raise ValueError(f"not an extension collection: {collection}")


class ControlPlaneHTTP(BaseControlPlaneHTTP):
    """HTTP mapping for the complete issue #32 resource and command surface."""

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
                return self._response(200, build_openapi(), request_id, correlation_id)
            if request.method == "GET" and relative in {"", "/"}:
                return self._response(
                    200,
                    {
                        "api_version": API_VERSION,
                        "resources": list(PLATFORM_COLLECTIONS) + ["timeline"],
                        "commands": list(REQUIRED_COMMANDS),
                        "openapi": f"/api/{API_VERSION}/openapi.json",
                        "live_updates": "sse",
                    },
                    request_id,
                    correlation_id,
                )

            segments = [segment for segment in relative.split("/") if segment]
            if segments and segments[0] in EXTENSION_COLLECTIONS:
                context = _request_context(request, request_id, correlation_id)
                query = _page_query(request.query)
                if len(segments) == 1 and request.method == "GET":
                    page = await self._extended_control_plane.list_extension_resources(
                        context,
                        segments[0],
                        query,
                    )
                    return self._response(200, page, request_id, correlation_id)
                if len(segments) == 2 and request.method == "GET" and ":" not in segments[1]:
                    item = await self._extended_control_plane.get_extension_resource(
                        context,
                        segments[0],
                        segments[1],
                    )
                    return self._response(200, item, request_id, correlation_id)
                command = _command_for_route(segments)
                if command is not None and request.method == "POST":
                    resource_ref = _resource_ref_for_route(segments)
                    item = await self._extended_control_plane.execute_command(
                        context,
                        command,
                        resource_ref,
                        request.body,
                    )
                    return self._response(200, item, request_id, correlation_id)

            if segments and segments[0] == "commands" and len(segments) == 2:
                if request.method != "POST":
                    raise APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    )
                context = _request_context(request, request_id, correlation_id)
                resource_ref = request.body.get("resource_ref")
                if not isinstance(resource_ref, str) or not resource_ref.strip():
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
                    resource_ref,
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


def build_openapi() -> dict[str, Any]:
    """Extend the base OpenAPI contract with every resource/command required by #32."""

    specification = deepcopy(build_base_openapi())
    paths = specification["paths"]
    assert isinstance(paths, dict)

    for collection in sorted(EXTENSION_COLLECTIONS):
        paths[f"/api/{API_VERSION}/{collection}"] = {
            "get": _list_operation(f"list{_pascal(collection)}", f"Canonical {collection} page")
        }
        paths[f"/api/{API_VERSION}/{collection}/{{resource_id}}"] = {
            "get": _read_operation(
                f"get{_pascal(collection)}Resource",
                f"Canonical {_singular(collection)} resource",
            )
        }

    command_routes = {
        "approval.approve": "/approvals/{resource_id}:approve",
        "approval.deny": "/approvals/{resource_id}:deny",
        "adapter.enable": "/adapters/{resource_id}:enable",
        "adapter.disable": "/adapters/{resource_id}:disable",
        "plugin.enable": "/plugins/{resource_id}:enable",
        "plugin.disable": "/plugins/{resource_id}:disable",
        "worker.drain": "/workers/{resource_id}:drain",
        "worker.restore": "/workers/{resource_id}:restore",
        "automation.test": "/automations/{resource_id}:test",
        "evaluation.start": "/evaluations/{resource_id}:start",
    }
    for command, route in command_routes.items():
        paths[f"/api/{API_VERSION}{route}"] = {
            "post": _command_operation(command.replace(".", "_") + "Command")
        }
    paths[f"/api/{API_VERSION}/commands/{{command}}"] = {
        "post": _command_operation("executeCanonicalCommand", command_parameter=True)
    }

    info = specification.get("info")
    if isinstance(info, dict):
        info["description"] = (
            "Stable platform-owned northbound API for canonical resources and explicit "
            "commands. Concrete orchestrator, executor, model, tool and worker-private APIs "
            "are never client contracts."
        )
    specification["x-platform-collections"] = list(PLATFORM_COLLECTIONS)
    specification["x-platform-commands"] = list(REQUIRED_COMMANDS)
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


def _command_operation(operation_id: str, *, command_parameter: bool = False) -> dict[str, Any]:
    parameters: list[dict[str, Any]] = [
        {
            "name": "Idempotency-Key",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "minLength": 1},
        }
    ]
    if command_parameter:
        parameters.append(
            {
                "name": "command",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "enum": list(REQUIRED_COMMANDS)},
            }
        )
    else:
        parameters.append(
            {
                "name": "resource_id",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    return {
        "operationId": operation_id,
        "parameters": parameters,
        "responses": {"200": {"description": "Canonical command result"}, **_error_responses()},
    }


def _error_responses() -> dict[str, Any]:
    return {
        status: {"$ref": "#/components/responses/Error"}
        for status in ("400", "401", "403", "404", "409", "429", "500", "502", "503", "504")
    }


def _command_for_route(segments: list[str]) -> str | None:
    if len(segments) != 2 or ":" not in segments[1]:
        return None
    collection = segments[0]
    _, action = segments[1].rsplit(":", 1)
    mapping = {
        ("approvals", "approve"): "approval.approve",
        ("approvals", "deny"): "approval.deny",
        ("adapters", "enable"): "adapter.enable",
        ("adapters", "disable"): "adapter.disable",
        ("plugins", "enable"): "plugin.enable",
        ("plugins", "disable"): "plugin.disable",
        ("workers", "drain"): "worker.drain",
        ("workers", "restore"): "worker.restore",
        ("automations", "test"): "automation.test",
        ("evaluations", "start"): "evaluation.start",
    }
    return mapping.get((collection, action))


def _resource_ref_for_route(segments: list[str]) -> str:
    resource_ref, _ = segments[1].rsplit(":", 1)
    if not resource_ref.strip():
        raise APIException(
            status=400,
            code="invalid_request",
            message="resource reference must not be blank",
        )
    return resource_ref


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
    leaked = forbidden.intersection(resource)
    if leaked:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            f"backend-private fields leaked through Control Plane: {sorted(leaked)!r}",
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
