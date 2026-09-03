"""Canonical Task planning projection and admission gates for issue #88."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, cast

from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import PlatformKernel, TaskState
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.task_management import TaskManagementService

from .extensions import CommandHandler, ResourceService
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext, paginate
from .observability_contract import ControlPlane as _ObservabilityControlPlane
from .observability_contract import ControlPlaneHTTP as _ObservabilityControlPlaneHTTP
from .observability_contract import build_openapi as _build_observability_openapi
from .service import ScopeStore, _task_resource

TASK_MANAGEMENT_UPDATE_COMMAND = "task-management.update"
TASK_MANAGEMENT_BULK_UPDATE_COMMAND = "task-management.bulk-update"
TASK_MANAGEMENT_COMMANDS = (
    TASK_MANAGEMENT_UPDATE_COMMAND,
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
)
TASK_MANAGEMENT_FIELDS = frozenset(
    {
        "priority",
        "due_at",
        "deadline_timezone",
        "not_before",
        "responsibility",
        "agent_assignment",
        "labels",
        "workspace_id",
        "parent_task_id",
        "dependencies",
        "blocking_reason",
        "effort_hint",
        "resource_hints",
        "archived",
        "hidden",
    }
)


class ControlPlane(_ObservabilityControlPlane):
    """Current Control Plane with one canonical Task management projection."""

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
        )
        self._task_management = task_management or TaskManagementService(
            kernel=kernel,
            workspace_project_resolver=self._workspace_project_id,
        )
        self.register_command(TASK_MANAGEMENT_UPDATE_COMMAND, self._update_management_command)
        self.register_command(
            TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
            self._bulk_update_management_command,
        )

    @property
    def task_management(self) -> TaskManagementService:
        return self._task_management

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        changes = {name: payload[name] for name in TASK_MANAGEMENT_FIELDS if name in payload}
        raw_task_id = payload.get("task_id")
        task_id = raw_task_id if isinstance(raw_task_id, str) else new_id("task")
        raw_project_id = payload.get("project_id")
        project_id = raw_project_id if isinstance(raw_project_id, str) else None
        if changes:
            await self._task_management.validate_new(
                task_id=task_id,
                project_id=project_id,
                changes=changes,
            )

        create_payload = dict(payload)
        create_payload["task_id"] = task_id
        resource = await super().create_task(context, create_payload)
        canonical_task_id = resource.get("id")
        if not isinstance(canonical_task_id, str):
            return resource
        if changes:
            key = _require_idempotency_key(context)
            prepared = await self._task_management.prepare(canonical_task_id, changes)
            await self._task_management.commit(
                prepared,
                idempotency_key=f"{key}:task-management",
                actor_ref=context.actor.principal_ref,
                source="control-plane",
            )
        return await self.get_task(context, canonical_task_id)

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "task:list", "tasks")
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            if await self._allowed_for_task(context, "task:list", task_id, task):
                resources.append(await self._managed_task_resource(task))
        return paginate(resources, _task_query(query))

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:read", task_id, task)
        return await self._managed_task_resource(task)

    async def queue_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:queue", task_id, task)
        await self._task_management.require_eligible(task_id)
        await super().queue_task(context, task_id)
        return await self.get_task(context, task_id)

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:start", task_id, task)
        await self._task_management.require_eligible(task_id)
        return await super().start_task(context, task_id)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:retry", task_id, task)
        await self._task_management.require_eligible(task_id)
        return await super().retry_task(context, task_id)

    async def _update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(resource_ref)
        await self._authorize_for_task(context, "task:update-management", resource_ref, task)
        key = _require_idempotency_key(context)
        prepared = await self._task_management.prepare(resource_ref, payload)
        await self._task_management.commit(
            prepared,
            idempotency_key=f"{key}:task-management",
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return await self.get_task(context, resource_ref)

    async def _bulk_update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != "tasks":
            raise ValueError("bulk task-management command resource_ref must be 'tasks'")
        raw_updates = payload.get("updates")
        if not isinstance(raw_updates, list) or not raw_updates:
            raise ValueError("updates must be a non-empty array")
        if len(raw_updates) > 100:
            raise ValueError("bulk task-management updates are limited to 100 items")

        parsed: list[tuple[str, dict[str, JsonValue]]] = []
        for raw_update in raw_updates:
            if not isinstance(raw_update, dict):
                raise ValueError("each bulk update must be an object")
            task_id = raw_update.get("task_id")
            changes = raw_update.get("changes")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ValueError("bulk update task_id must be a non-blank string")
            if not isinstance(changes, dict) or not changes:
                raise ValueError("bulk update changes must be a non-empty object")
            parsed.append((task_id, changes))

        for task_id, _ in parsed:
            task = await self._kernel.get_task(task_id)
            await self._authorize_for_task(context, "task:update-management", task_id, task)

        prepared = await self._task_management.prepare_batch(parsed)
        key = _require_idempotency_key(context)
        applied: list[JsonValue] = []
        for index, update in enumerate(prepared):
            view = await self._task_management.commit(
                update,
                idempotency_key=f"{key}:task-management:{index}",
                actor_ref=context.actor.principal_ref,
                source="control-plane",
            )
            applied.append({"task_id": update.task.task_id, "eligible": view.eligible})
        return {
            "id": f"bulk:{key}",
            "type": "task-management-bulk-result",
            "atomic": False,
            "authorization_preflighted": True,
            "count": len(applied),
            "items": applied,
        }

    async def _managed_task_resource(self, state: TaskState) -> dict[str, JsonValue]:
        resource = _task_resource(state)
        view = await self._task_management.view(state)
        planning = view.planning_resource()
        management_blocked = planning.pop("blocked")
        resource.update(planning)
        resource["management_blocked"] = management_blocked
        resource["blocked"] = bool(state.blocked or management_blocked)
        if resource.get("effective_blocking_reason") is None and state.wait_reason is not None:
            resource["effective_blocking_reason"] = state.wait_reason
        return resource

    def _workspace_project_id(self, workspace_id: str) -> str:
        return self.scopes.get_workspace(workspace_id).project_id


class ControlPlaneHTTP(_ObservabilityControlPlaneHTTP):
    """HTTP transport that publishes the Task-management extension metadata."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_openapi(cast(dict[str, Any], deepcopy(response.body)))
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
    commands = tuple(sorted(set((*extension_commands, *TASK_MANAGEMENT_COMMANDS))))
    return _augment_openapi(
        _build_observability_openapi(
            extension_collections=extension_collections,
            extension_commands=commands,
        )
    )


def _task_query(query: PageQuery) -> PageQuery:
    sort = "priority_rank" if query.sort == "priority" else query.sort
    if sort == "due":
        sort = "due_at"
    return PageQuery(
        limit=query.limit,
        cursor=query.cursor,
        sort=sort,
        direction=query.direction,
        search=query.search,
        filters=query.filters,
        fields=query.fields,
    )


def _require_idempotency_key(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ValueError("Idempotency-Key is required for task-management commands")
    return context.idempotency_key


def _augment_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    components = specification.get("components")
    if isinstance(components, dict):
        schemas = components.get("schemas")
        if isinstance(schemas, dict):
            create_task = schemas.get("CreateTaskRequest")
            if isinstance(create_task, dict):
                properties = create_task.get("properties")
                if isinstance(properties, dict):
                    properties.update(
                        {
                            "priority": {
                                "type": "string",
                                "enum": ["low", "normal", "high", "urgent"],
                            },
                            "due_at": {"type": ["string", "null"], "format": "date-time"},
                            "deadline_timezone": {"type": ["string", "null"]},
                            "not_before": {
                                "type": ["string", "null"],
                                "format": "date-time",
                            },
                            "responsibility": {"type": ["object", "null"]},
                            "agent_assignment": {"type": ["object", "null"]},
                            "labels": {"type": "array", "items": {"type": "string"}},
                            "workspace_id": {"type": ["string", "null"]},
                            "parent_task_id": {"type": ["string", "null"]},
                            "dependencies": {"type": "array", "items": {"type": "object"}},
                            "blocking_reason": {"type": ["string", "null"]},
                            "effort_hint": {
                                "type": ["number", "null"],
                                "exclusiveMinimum": 0,
                            },
                            "resource_hints": {
                                "type": "object",
                                "additionalProperties": True,
                            },
                            "archived": {"type": "boolean"},
                            "hidden": {"type": "boolean"},
                        }
                    )
    specification["x-task-management"] = {
        "metadata_owner": "canonical Task metadata / platform kernel event history",
        "priority_order": ["low", "normal", "high", "urgent"],
        "deadline_is_schedule": False,
        "responsibility_grants_authorization": False,
        "dependency_policy": "same-project canonical Task IDs; depends_on blocks until succeeded",
        "admission_gates": ["dependencies", "not_before", "manual_block", "archived"],
        "commands": list(TASK_MANAGEMENT_COMMANDS),
    }
    return specification
