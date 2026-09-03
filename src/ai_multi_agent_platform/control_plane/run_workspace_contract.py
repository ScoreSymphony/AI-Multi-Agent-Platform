"""Exact Workspace snapshot bindings for canonical Run API resources."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

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
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBinding,
    RunWorkspaceBindingRepository,
    Workspace,
    WorkspaceProvider,
    WorkspaceSnapshot,
)

from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext
from .service import ScopeStore, _optional_string, _required_string, _require_key
from .workspace_contract import ControlPlane as _WorkspaceControlPlane
from .workspace_contract import ControlPlaneHTTP as _WorkspaceControlPlaneHTTP
from .workspace_contract import build_openapi as _build_workspace_openapi

_BINDING_FIELDS = frozenset({"workspace_id", "workspace_snapshot_id"})


class ControlPlane(_WorkspaceControlPlane):
    """Composed Control Plane that can bind a Run to one immutable WorkspaceSnapshot."""

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
        workspace_provider: WorkspaceProvider | None = None,
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
    ) -> None:
        super().__init__(
            kernel=kernel,
            events=events,
            scopes=scopes,
            authorization=authorization,
            live_events=live_events,
            health_providers=health_providers,
            model_registry=model_registry,
            workspace_provider=workspace_provider,
        )
        self._run_workspace_bindings = run_workspace_bindings

    @property
    def run_workspace_bindings(self) -> RunWorkspaceBindingRepository | None:
        return self._run_workspace_bindings

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if not _has_binding_fields(payload):
            return await self._decorate_binding(await super().start_task(context, task_id))

        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:start", task_id, task)
        workspace, snapshot = await self._resolve_workspace_input(context, task_id, payload or {})
        key = _require_key(context)
        if task.plan_ref is None:
            await self._kernel.plan_task(
                idempotency_key=f"{key}:plan",
                task_id=task_id,
                actor_ref=context.actor.principal_ref,
                source="control-plane",
            )
        run = await self._kernel.create_run(
            idempotency_key=f"{key}:create-run",
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        await self._bind_run(run.run_id, task_id, workspace, snapshot)
        await self._kernel.start_run(
            idempotency_key=f"{key}:start-run",
            task_id=task_id,
            run_id=run.run_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        resource = await super().get_run(context, run.run_id, task_id=task_id)
        return await self._decorate_binding(resource)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        explicit_binding = _has_binding_fields(payload)
        previous_binding = None
        if not explicit_binding and self._run_workspace_bindings is not None:
            previous_binding = await self._latest_binding(task.run_ids)

        if not explicit_binding and previous_binding is None:
            return await self._decorate_binding(await super().retry_task(context, task_id))

        await self._authorize_for_task(context, "task:retry", task_id, task)
        key = _require_key(context)
        workspace: Workspace | None = None
        snapshot: WorkspaceSnapshot | None = None
        if explicit_binding:
            workspace, snapshot = await self._resolve_workspace_input(context, task_id, payload or {})

        run = await self._kernel.retry_task(
            idempotency_key=key,
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        if workspace is not None and snapshot is not None:
            await self._bind_run(run.run_id, task_id, workspace, snapshot)
        elif previous_binding is not None:
            await self._bind_existing_target(run.run_id, task_id, previous_binding)
        resource = await super().get_run(context, run.run_id, task_id=task_id)
        return await self._decorate_binding(resource)

    async def list_runs(
        self,
        context: RequestContext,
        query: PageQuery,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        page = await super().list_runs(context, query, task_id=task_id)
        items = page.get("items")
        if not isinstance(items, list):
            return page
        decorated: list[JsonValue] = []
        for item in items:
            if isinstance(item, dict):
                decorated.append(await self._decorate_binding(cast(dict[str, JsonValue], item)))
            else:
                decorated.append(item)
        result = dict(page)
        result["items"] = decorated
        return result

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        return await self._decorate_binding(
            await super().get_run(context, run_id, task_id=task_id)
        )

    async def _resolve_workspace_input(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue],
    ) -> tuple[Workspace, WorkspaceSnapshot]:
        provider = self.workspace_provider
        if provider is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical WorkspaceProvider is required for Run workspace binding",
                retryable=True,
            )
        self._require_binding_repository()
        task = await self._kernel.get_task(task_id)
        workspace_id = _required_string(payload, "workspace_id")
        workspace = await provider.get_workspace(workspace_id)
        if task.task.project_id is None or workspace.project_id != task.task.project_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Run workspace must belong to the same project as the Task",
            )
        snapshot_id = _optional_string(payload, "workspace_snapshot_id") or workspace.base_snapshot_id
        if snapshot_id is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workspace has no canonical base snapshot",
            )
        snapshot = await provider.get_snapshot(snapshot_id)
        if snapshot.workspace_id != workspace.id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "workspace_snapshot_id belongs to a different workspace",
                details={"field": "workspace_snapshot_id"},
            )
        await self._authorize(
            context,
            "workspace:use",
            workspace.id,
            owner_type=workspace.owner_ref.type,
            owner_id=workspace.owner_ref.id,
            project_id=workspace.project_id,
        )
        return workspace, snapshot

    async def _bind_run(
        self,
        run_id: str,
        task_id: str,
        workspace: Workspace,
        snapshot: WorkspaceSnapshot,
    ) -> RunWorkspaceBinding:
        repository = self._require_binding_repository()
        existing = await repository.get(run_id)
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.workspace_id != workspace.id
                or existing.workspace_snapshot_id != snapshot.id
                or existing.content_checksum != snapshot.content_checksum
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run already has a different workspace input: {run_id}",
                )
            return existing
        return await repository.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=workspace.id,
                workspace_snapshot_id=snapshot.id,
                content_checksum=snapshot.content_checksum,
            )
        )

    async def _bind_existing_target(
        self,
        run_id: str,
        task_id: str,
        previous: RunWorkspaceBinding,
    ) -> RunWorkspaceBinding:
        repository = self._require_binding_repository()
        existing = await repository.get(run_id)
        if existing is not None:
            if (
                existing.task_id != task_id
                or existing.workspace_id != previous.workspace_id
                or existing.workspace_snapshot_id != previous.workspace_snapshot_id
                or existing.content_checksum != previous.content_checksum
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run already has a different workspace input: {run_id}",
                )
            return existing
        return await repository.bind(
            RunWorkspaceBinding(
                run_id=run_id,
                task_id=task_id,
                workspace_id=previous.workspace_id,
                workspace_snapshot_id=previous.workspace_snapshot_id,
                content_checksum=previous.content_checksum,
            )
        )

    async def _latest_binding(self, run_ids: tuple[str, ...]) -> RunWorkspaceBinding | None:
        repository = self._require_binding_repository()
        for run_id in reversed(run_ids):
            binding = await repository.get(run_id)
            if binding is not None:
                return binding
        return None

    async def _decorate_binding(
        self,
        resource: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        repository = self._run_workspace_bindings
        run_id = resource.get("id")
        if repository is None or not isinstance(run_id, str):
            return resource
        binding = await repository.get(run_id)
        if binding is None:
            return resource
        decorated = dict(resource)
        decorated["workspace_id"] = binding.workspace_id
        decorated["workspace_snapshot_id"] = binding.workspace_snapshot_id
        decorated["workspace_content_checksum"] = binding.content_checksum
        return decorated

    def _require_binding_repository(self) -> RunWorkspaceBindingRepository:
        if self._run_workspace_bindings is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "Run workspace binding repository is not configured",
                retryable=True,
            )
        return self._run_workspace_bindings


class ControlPlaneHTTP(_WorkspaceControlPlaneHTTP):
    """HTTP mapper for workspace-aware start/retry commands and Run fields."""

    async def _tasks(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if len(segments) == 2 and ":" in segments[1] and request.method == "POST":
            task_id, command = segments[1].split(":", 1)
            if command in {"start", "retry"} and _has_binding_fields(request.body):
                control_plane = cast(ControlPlane, self._control_plane)
                if command == "start":
                    item = await control_plane.start_task(context, task_id, request.body)
                else:
                    item = await control_plane.retry_task(context, task_id, request.body)
                return self._response(200, item, request_id, correlation_id)
        return await super()._tasks(
            request,
            context,
            query,
            segments,
            request_id,
            correlation_id,
        )

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_run_workspace_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
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
    specification = _build_workspace_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    return _augment_run_workspace_openapi(deepcopy(specification))


def _has_binding_fields(payload: dict[str, JsonValue] | None) -> bool:
    return payload is not None and any(name in payload for name in _BINDING_FIELDS)


def _augment_run_workspace_openapi(specification: dict[str, Any]) -> dict[str, Any]:
    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    run_schema = schemas.get("Run")
    if isinstance(run_schema, dict):
        properties = run_schema.setdefault("properties", {})
        properties["workspace_id"] = {"type": "string"}
        properties["workspace_snapshot_id"] = {"type": "string"}
        properties["workspace_content_checksum"] = {
            "type": "string",
            "pattern": "^[0-9a-f]{64}$",
        }
    return specification
