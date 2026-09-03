"""Canonical Control Plane integration for platform-wide derived Search (#45)."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
from typing import Any, Literal, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import (
    JsonValue,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.search import (
    LocalSearchProvider,
    SearchDocument,
    SearchMode,
    SearchProvider,
    SearchQuery,
    SearchResult,
    SearchService,
    document_from_resource,
)
from ai_multi_agent_platform.task_management import TaskManagementService
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    WorkspaceProvider,
)

from .extensions import CommandHandler, ResourceService
from .http import (
    HTTPRequest,
    HTTPResponse,
    _header,
    _request_context,
    _require_supported_version,
    _split_version,
)
from .models import API_VERSION, APIException, RequestContext, api_exception_from_contract
from .service import (
    ScopeStore,
    _project_resource,
    _run_resource,
    _task_resource,
    _workspace_resource,
)
from .workspace_task_management_api import ControlPlane as _BaseControlPlane
from .workspace_task_management_api import ControlPlaneHTTP as _BaseControlPlaneHTTP
from .workspace_task_management_api import build_openapi as _build_base_openapi


class ControlPlane(_BaseControlPlane):
    """Current composed Control Plane plus a replaceable, derived SearchProvider."""

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
        task_management: TaskManagementService | None = None,
        workspace_provider: WorkspaceProvider | None = None,
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
        search_provider: SearchProvider | None = None,
    ) -> None:
        super().__init__(
            kernel=kernel,
            events=events,
            scopes=scopes,
            authorization=authorization,
            live_events=live_events,
            health_providers=health_providers,
            model_registry=model_registry,
            resource_services=resource_services,
            command_handlers=command_handlers,
            task_management=task_management,
            workspace_provider=workspace_provider,
            run_workspace_bindings=run_workspace_bindings,
        )
        self._search_provider = search_provider or LocalSearchProvider()
        self._search_service = SearchService(self._search_provider)

    @property
    def search_provider(self) -> SearchProvider:
        return self._search_provider

    async def rebuild_search_index(self, *, correlation_id: str = "search-rebuild") -> int:
        """Reconstruct all Stage-1 derived Search state from canonical sources."""

        documents: list[SearchDocument] = []
        for project in self._scopes.list_projects():
            documents.append(document_from_resource(_project_resource(project)))

        workspace_provider = self.workspace_provider
        if workspace_provider is None:
            for legacy_workspace in self._scopes.list_workspaces():
                documents.append(document_from_resource(_workspace_resource(legacy_workspace)))
        else:
            for canonical_workspace in await workspace_provider.list_workspaces():
                documents.append(
                    SearchDocument(
                        resource_type="workspace",
                        resource_id=canonical_workspace.id,
                        title=f"Workspace {canonical_workspace.id}",
                        project_id=canonical_workspace.project_id,
                        workspace_id=canonical_workspace.id,
                        owner_type=canonical_workspace.owner_ref.type,
                        owner_id=canonical_workspace.owner_ref.id,
                        keywords=(
                            "workspace",
                            canonical_workspace.id,
                            canonical_workspace.project_id,
                        ),
                        updated_at=canonical_workspace.updated_at.isoformat(),
                        canonical_ref=f"/api/{API_VERSION}/workspaces/{canonical_workspace.id}",
                        provenance={"indexed_from": "canonical-workspace-provider"},
                    )
                )

        binding_repository = self.run_workspace_bindings
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            documents.append(document_from_resource(_task_resource(task)))
            for run_id in task.run_ids:
                run = await self._kernel.get_run(task_id, run_id)
                run_document = document_from_resource(_run_resource(run))
                workspace_id = None
                if binding_repository is not None:
                    binding = await binding_repository.get(run_id)
                    if binding is not None:
                        workspace_id = binding.workspace_id
                documents.append(
                    replace(
                        run_document,
                        workspace_id=workspace_id,
                        owner_type=task.task.owner_ref.type,
                        owner_id=task.task.owner_ref.id,
                    )
                )

        await self._search_provider.rebuild(
            tuple(documents),
            OperationContext(correlation_id=correlation_id),
        )
        return len(documents)

    async def search_resources(
        self,
        context: RequestContext,
        query: SearchQuery,
    ) -> dict[str, JsonValue]:
        """Search current canonical resources without exposing unauthorized candidates.

        Stage 1 intentionally refreshes the derived local/provider index from canonical
        sources before a query. This correctness-first baseline guarantees update/delete
        propagation without requiring an event bus or external search service. Durable
        providers can later replace this with checkpointed write-through/event indexing.
        """

        await self.rebuild_search_index(correlation_id=context.correlation_id)
        operation = OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=query.project_id,
            control=OperationControl(),
        )
        page = await self._search_service.search(
            query,
            operation,
            lambda result: self._search_result_allowed(context, result),
        )
        return page.to_json()

    async def _search_result_allowed(
        self,
        context: RequestContext,
        result: SearchResult,
    ) -> bool:
        action = {
            "project": "project:list",
            "workspace": "workspace:list",
            "task": "task:list",
            "run": "run:list",
        }.get(result.resource_type)
        if action is None:
            return False
        return await self._allowed(
            context,
            action,
            result.resource_id,
            owner_type=result.owner_type,
            owner_id=result.owner_id,
            project_id=result.project_id,
        )


class ControlPlaneHTTP(_BaseControlPlaneHTTP):
    """Publish the canonical Stage-1 global Search endpoint."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            version, relative = _split_version(request.path)
            _require_supported_version(version)

            if relative == "/search":
                if request.method != "GET":
                    raise APIException(
                        status=405,
                        code="method_not_allowed",
                        message="method not allowed",
                    )
                context = _request_context(request, request_id, correlation_id)
                page = await cast(ControlPlane, self._control_plane).search_resources(
                    context,
                    _search_query(request),
                )
                return self._response(200, page, request_id, correlation_id)
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

        response = await super().handle(request)
        if (
            request.method == "GET"
            and relative == "/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_search_openapi(cast(dict[str, Any], deepcopy(response.body)))
            return HTTPResponse(
                status=response.status,
                body=cast(dict[str, JsonValue], specification),
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    specification = deepcopy(
        _build_base_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )
    )
    return _augment_search_openapi(specification)


def _search_query(request: HTTPRequest) -> SearchQuery:
    params = request.query
    text = _optional_query(params, "q")
    exact_id = _optional_query(params, "id")
    mode_value = _optional_query(params, "mode")
    if mode_value is None:
        mode = SearchMode.EXACT if exact_id is not None and text is None else SearchMode.KEYWORD
        if exact_id is None and text is None:
            mode = SearchMode.METADATA
    else:
        mode = SearchMode(mode_value)
    limit_value = _optional_query(params, "limit")
    limit = int(limit_value) if limit_value is not None else 50
    direction_value = _optional_query(params, "direction") or "desc"
    if direction_value not in {"asc", "desc"}:
        raise ValueError("direction must be asc or desc")
    sort_value = _optional_query(params, "sort") or "relevance"
    if sort_value not in {"relevance", "id", "updated_at"}:
        raise ValueError("sort must be relevance, id or updated_at")
    return SearchQuery(
        text=text,
        exact_id=exact_id,
        resource_types=_csv_query(params, "type"),
        project_id=_optional_query(params, "project_id"),
        workspace_id=_optional_query(params, "workspace_id"),
        statuses=_csv_query(params, "status"),
        tags=_csv_query(params, "tag"),
        source_filters=_csv_query(params, "source"),
        provider_filters=_csv_query(params, "provider"),
        updated_after=_timestamp_query(params, "updated_after"),
        updated_before=_timestamp_query(params, "updated_before"),
        mode=mode,
        limit=limit,
        cursor=_optional_query(params, "cursor"),
        sort=cast(Literal["relevance", "id", "updated_at"], sort_value),
        direction=cast(Literal["asc", "desc"], direction_value),
    )


def _optional_query(params: Mapping[str, str], name: str) -> str | None:
    value = params.get(name)
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized


def _csv_query(params: Mapping[str, str], name: str) -> tuple[str, ...]:
    value = _optional_query(params, name)
    if value is None:
        return ()
    values = tuple(part.strip() for part in value.split(",") if part.strip())
    if not values:
        raise ValueError(f"{name} must contain at least one value")
    return values


def _timestamp_query(params: Mapping[str, str], name: str) -> datetime | None:
    value = _optional_query(params, name)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed


def _augment_search_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    paths = specification.setdefault("paths", {})
    components = specification.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas["SearchResult"] = {
        "type": "object",
        "required": ["resource_type", "resource_id", "title", "relevance", "access"],
        "properties": {
            "resource_type": {"type": "string"},
            "resource_id": {"type": "string"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "project_id": {"type": ["string", "null"]},
            "workspace_id": {"type": ["string", "null"]},
            "status": {"type": ["string", "null"]},
            "relevance": {"type": "number"},
            "matched_fields": {"type": "array", "items": {"type": "string"}},
            "canonical_ref": {"type": ["string", "null"]},
            "access": {"type": "string", "enum": ["authorized"]},
            "redacted": {"type": "boolean"},
        },
    }
    schemas["SearchPage"] = {
        "type": "object",
        "required": ["items", "total", "limit", "next_cursor"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/SearchResult"},
            },
            "total": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            "next_cursor": {"type": ["string", "null"]},
        },
    }
    paths[f"/api/{API_VERSION}/search"] = {
        "get": {
            "operationId": "searchPlatformResources",
            "summary": "Search authorized canonical platform resources",
            "parameters": [
                {"name": "q", "in": "query", "schema": {"type": "string"}},
                {"name": "id", "in": "query", "schema": {"type": "string"}},
                {"name": "type", "in": "query", "schema": {"type": "string"}},
                {"name": "project_id", "in": "query", "schema": {"type": "string"}},
                {"name": "workspace_id", "in": "query", "schema": {"type": "string"}},
                {"name": "status", "in": "query", "schema": {"type": "string"}},
                {"name": "tag", "in": "query", "schema": {"type": "string"}},
                {
                    "name": "updated_after",
                    "in": "query",
                    "schema": {"type": "string", "format": "date-time"},
                },
                {
                    "name": "updated_before",
                    "in": "query",
                    "schema": {"type": "string", "format": "date-time"},
                },
                {
                    "name": "mode",
                    "in": "query",
                    "schema": {
                        "type": "string",
                        "enum": [mode.value for mode in SearchMode],
                    },
                },
                {
                    "name": "limit",
                    "in": "query",
                    "schema": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                {"name": "cursor", "in": "query", "schema": {"type": "string"}},
                {
                    "name": "sort",
                    "in": "query",
                    "schema": {"type": "string", "enum": ["relevance", "id", "updated_at"]},
                },
                {
                    "name": "direction",
                    "in": "query",
                    "schema": {"type": "string", "enum": ["asc", "desc"]},
                },
            ],
            "responses": {
                "200": {
                    "description": "Authorization-filtered search results",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/SearchPage"}}
                    },
                }
            },
        }
    }
    return specification
