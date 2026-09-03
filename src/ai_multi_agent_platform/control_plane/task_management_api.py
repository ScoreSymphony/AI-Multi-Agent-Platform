"""Public Control Plane composition for canonical Task management.

Built-in Task-management commands are intentionally kept separate from the
future-extension command registry introduced by issue #32. They share the
same HTTP command transport, authorization boundary and idempotency rules,
but do not make the extension registry claim built-in platform behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.task_management import TaskManagementService

from .extensions import CommandHandler, ResourceService
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext, paginate
from .observability_contract import build_openapi as _build_observability_openapi
from .service import ScopeStore
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_COMMANDS,
    TASK_MANAGEMENT_UPDATE_COMMAND,
    _augment_openapi,
)
from .task_management_contract import (
    ControlPlane as _TaskManagementControlPlane,
)
from .task_management_contract import (
    ControlPlaneHTTP as _TaskManagementControlPlaneHTTP,
)

_CUSTOM_QUEUE_FILTERS = frozenset({"due_after", "due_before", "assignment_state"})


class ControlPlane(_TaskManagementControlPlane):
    """Task-management Control Plane with built-ins separated from extensions."""

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
        )
        # The parent implementation originally used the generic extension
        # registry as a transport hook. Remove only the platform-owned commands
        # so `registered_commands` keeps its issue-#32 meaning: external/future
        # extension registrations supplied by composition.
        self._command_handlers.pop(TASK_MANAGEMENT_UPDATE_COMMAND, None)
        self._command_handlers.pop(TASK_MANAGEMENT_BULK_UPDATE_COMMAND, None)

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        """List managed Tasks with canonical derived queue filters."""

        await self._authorize(context, "task:list", "tasks")
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            if await self._allowed_for_task(context, "task:list", task_id, task):
                resources.append(await self._managed_task_resource(task))
        ranged = _filter_custom_queue_state(resources, query)
        return paginate(ranged, _task_page_query(query))

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in TASK_MANAGEMENT_COMMANDS:
            return await super().execute_command(context, command, resource_ref, payload)
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        await self._authorize(context, command, resource_ref)
        body = payload or {}
        if command == TASK_MANAGEMENT_UPDATE_COMMAND:
            return await self._update_management_command(context, resource_ref, body)
        return await self._bulk_update_management_command(context, resource_ref, body)


class ControlPlaneHTTP(_TaskManagementControlPlaneHTTP):
    """HTTP transport exposing explicit OpenAPI paths for Task-management commands."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = deepcopy(response.body)
            _add_task_management_paths(specification)
            _add_task_management_query_contract(specification)
            return HTTPResponse(
                status=response.status,
                body=specification,
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build OpenAPI with native #88 commands separate from extension discovery."""

    specification = _augment_openapi(
        _build_observability_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )
    )
    _add_task_management_paths(specification)
    _add_task_management_query_contract(specification)
    return specification


def _filter_custom_queue_state(
    resources: list[dict[str, JsonValue]],
    query: PageQuery,
) -> list[dict[str, JsonValue]]:
    filters = query.filters or {}
    due_after = _parse_deadline_boundary(filters.get("due_after"), "due_after")
    due_before = _parse_deadline_boundary(filters.get("due_before"), "due_before")
    if due_after is not None and due_before is not None and due_after > due_before:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "filter[due_after] must be earlier than or equal to filter[due_before]",
        )

    assignment_state = filters.get("assignment_state")
    if assignment_state not in {None, "assigned", "unassigned"}:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "filter[assignment_state] must be assigned or unassigned",
        )

    filtered: list[dict[str, JsonValue]] = []
    for resource in resources:
        if assignment_state is not None:
            assigned = (
                resource.get("responsible_id") is not None
                or resource.get("agent_assignment_id") is not None
            )
            if assignment_state == "assigned" and not assigned:
                continue
            if assignment_state == "unassigned" and assigned:
                continue

        if due_after is not None or due_before is not None:
            raw_due = resource.get("due_at")
            if not isinstance(raw_due, str):
                continue
            due = _parse_deadline_boundary(raw_due, "due_at")
            if due is None:
                continue
            if due_after is not None and due < due_after:
                continue
            if due_before is not None and due > due_before:
                continue

        filtered.append(resource)
    return filtered


def _parse_deadline_boundary(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"filter[{name}] must be a valid ISO 8601 datetime",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"filter[{name}] must include a timezone offset",
        )
    return parsed


def _task_page_query(query: PageQuery) -> PageQuery:
    sort = "priority_rank" if query.sort == "priority" else query.sort
    if sort == "due":
        sort = "due_at"
    filters = {
        name: value
        for name, value in (query.filters or {}).items()
        if name not in _CUSTOM_QUEUE_FILTERS
    }
    return PageQuery(
        limit=query.limit,
        cursor=query.cursor,
        sort=sort,
        direction=query.direction,
        search=query.search,
        filters=filters or None,
        fields=query.fields,
    )


def _add_task_management_paths(specification: dict[str, Any]) -> None:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return
    for command, operation_id, summary in (
        (
            TASK_MANAGEMENT_UPDATE_COMMAND,
            "updateTaskManagement",
            "Update canonical Task planning metadata",
        ),
        (
            TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
            "bulkUpdateTaskManagement",
            "Bulk update canonical Task planning metadata",
        ),
    ):
        paths[f"/api/{API_VERSION}/commands/{command}"] = {
            "post": {
                "operationId": operation_id,
                "summary": summary,
                "parameters": [
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["resource_ref"],
                                "properties": {"resource_ref": {"type": "string", "minLength": 1}},
                                "additionalProperties": True,
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Canonical Task-management command result"},
                    "400": {"description": "Invalid request"},
                    "403": {"description": "Forbidden"},
                    "409": {"description": "Conflict"},
                },
            }
        }


def _add_task_management_query_contract(specification: dict[str, Any]) -> None:
    extension = specification.get("x-task-management")
    if not isinstance(extension, dict):
        extension = {}
        specification["x-task-management"] = extension
    extension["deadline_range_filters"] = {
        "due_after": "inclusive ISO 8601 lower bound",
        "due_before": "inclusive ISO 8601 upper bound",
    }
    extension["queue_filters"] = [
        "project_id",
        "responsible_type",
        "responsible_id",
        "agent_assignment_type",
        "agent_assignment_id",
        "assignment_state",
        "blocked",
        "overdue",
    ]
