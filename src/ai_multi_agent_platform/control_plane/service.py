"""Framework-independent application service for the v1 Control Plane."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Literal, cast

from ai_multi_agent_platform.contracts.authorization import AuthorizationRequest
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ModelProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    JsonValue,
    OperationContext,
    OperationControl,
)
from ai_multi_agent_platform.domain import OwnerRef, Project, new_id, validate_id
from ai_multi_agent_platform.kernel import PlatformKernel, RunState, TaskState
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelConfiguration, ModelRegistry

from .models import (
    ActorContext,
    OwnerType,
    PageQuery,
    RequestContext,
    WorkspaceIdentity,
    json_object,
    paginate,
)

ReferenceCollection = Literal["plans", "steps", "artifacts", "results"]


class ScopeStore:
    """Minimal project/workspace identity store; #37 replaces workspace lifecycle internals."""

    def __init__(self) -> None:
        self._projects: dict[str, Project] = {}
        self._workspaces: dict[str, WorkspaceIdentity] = {}
        self._commands: dict[tuple[str, str], str] = {}

    def create_project(
        self,
        *,
        key: str,
        name: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
    ) -> Project:
        existing = self._commands.get(("project.create", key))
        if existing is not None:
            return self.get_project(existing)
        canonical_id = project_id or new_id("project")
        validate_id(canonical_id, "project")
        if canonical_id in self._projects:
            raise ContractError(ErrorCode.CONFLICT, f"project already exists: {canonical_id}")
        project = Project(
            id=canonical_id,
            name=name,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
        )
        self._projects[canonical_id] = project
        self._commands[("project.create", key)] = canonical_id
        return project

    def get_project(self, project_id: str) -> Project:
        validate_id(project_id, "project")
        try:
            return self._projects[project_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"project not found: {project_id}") from exc

    def list_projects(self) -> tuple[Project, ...]:
        return tuple(self._projects.values())

    def create_workspace(
        self,
        *,
        key: str,
        project_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceIdentity:
        existing = self._commands.get(("workspace.create", key))
        if existing is not None:
            return self.get_workspace(existing)
        project = self.get_project(project_id)
        workspace = WorkspaceIdentity(
            id=workspace_id or "",
            project_id=project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
        )
        if workspace.id in self._workspaces:
            raise ContractError(ErrorCode.CONFLICT, f"workspace already exists: {workspace.id}")
        self._workspaces[workspace.id] = workspace
        self._commands[("workspace.create", key)] = workspace.id
        return workspace

    def get_workspace(self, workspace_id: str) -> WorkspaceIdentity:
        validate_id(workspace_id, "workspace")
        try:
            return self._workspaces[workspace_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"workspace not found: {workspace_id}",
            ) from exc

    def list_workspaces(self) -> tuple[WorkspaceIdentity, ...]:
        return tuple(self._workspaces.values())


class ControlPlane:
    """Stable northbound boundary over canonical domain/kernel/provider contracts."""

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
    ) -> None:
        self._kernel = kernel
        self._events = events
        self._scopes = scopes or ScopeStore()
        self._authorization = authorization
        self._live_events = live_events
        self._health_providers = health_providers
        self._model_registry = model_registry

    @property
    def scopes(self) -> ScopeStore:
        return self._scopes

    async def health(self) -> dict[str, JsonValue]:
        providers: list[JsonValue] = []
        ready = True
        for provider in self._health_providers:
            descriptor = provider.descriptor
            status = await provider.health()
            if not descriptor.available or status.value == "unavailable":
                ready = False
            providers.append(
                {
                    "id": descriptor.provider_id,
                    "type": descriptor.provider_type,
                    "status": status.value,
                    "available": descriptor.available,
                }
            )
        return {
            "status": "healthy",
            "ready": ready,
            "api_version": "v1",
            "providers": providers,
        }

    async def create_project(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        owner_type, owner_id = _resolve_owner(context.actor, payload)
        await self._authorize(
            context,
            "project:create",
            "projects",
            owner_type=owner_type,
            owner_id=owner_id,
            request_payload_digest=_payload_digest(payload),
        )
        project = self._scopes.create_project(
            key=_require_key(context),
            name=_required_string(payload, "name"),
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=_optional_string(payload, "project_id"),
        )
        return _project_resource(project)

    async def list_projects(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "project:list", "projects")
        resources: list[dict[str, JsonValue]] = []
        for project in self._scopes.list_projects():
            if await self._allowed(
                context,
                "project:list",
                project.id,
                owner_type=project.owner_ref.type,
                owner_id=project.owner_ref.id,
                project_id=project.id,
            ):
                resources.append(_project_resource(project))
        return paginate(resources, query)

    async def get_project(
        self,
        context: RequestContext,
        project_id: str,
    ) -> dict[str, JsonValue]:
        project = self._scopes.get_project(project_id)
        await self._authorize(
            context,
            "project:read",
            project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
        )
        return _project_resource(project)

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        project_id = _required_string(payload, "project_id")
        project = self._scopes.get_project(project_id)
        await self._authorize(
            context,
            "workspace:create",
            project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
            request_payload_digest=_payload_digest(payload),
        )
        workspace = self._scopes.create_workspace(
            key=_require_key(context),
            project_id=project_id,
            workspace_id=_optional_string(payload, "workspace_id"),
        )
        return _workspace_resource(workspace)

    async def list_workspaces(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "workspace:list", "workspaces")
        resources: list[dict[str, JsonValue]] = []
        for workspace in self._scopes.list_workspaces():
            if await self._allowed(
                context,
                "workspace:list",
                workspace.id,
                owner_type=workspace.owner_type,
                owner_id=workspace.owner_id,
                project_id=workspace.project_id,
            ):
                resources.append(_workspace_resource(workspace))
        return paginate(resources, query)

    async def get_workspace(
        self,
        context: RequestContext,
        workspace_id: str,
    ) -> dict[str, JsonValue]:
        workspace = self._scopes.get_workspace(workspace_id)
        await self._authorize(
            context,
            "workspace:read",
            workspace_id,
            owner_type=workspace.owner_type,
            owner_id=workspace.owner_id,
            project_id=workspace.project_id,
        )
        return _workspace_resource(workspace)

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        owner_type, owner_id = _resolve_owner(context.actor, payload)
        project_id = _optional_string(payload, "project_id")
        if project_id is not None:
            self._scopes.get_project(project_id)
        await self._authorize(
            context,
            "task:create",
            "tasks",
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=_payload_digest(payload),
        )
        task = await self._kernel.create_task(
            idempotency_key=_require_key(context),
            title=_required_string(payload, "title"),
            objective=_required_string(payload, "objective"),
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            task_id=_optional_string(payload, "task_id"),
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _task_resource(task)

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
                resources.append(_task_resource(task))
        return paginate(resources, query)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:read", task_id, task)
        return _task_resource(task)

    async def queue_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:queue", task_id, task)
        state = await self._kernel.ready_task(
            idempotency_key=_require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _task_resource(state)

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:start", task_id, task)
        state = await self._kernel.start_task(
            idempotency_key=_require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _run_resource(state)

    async def cancel_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:cancel", task_id, task)
        state = await self._kernel.cancel_task(
            idempotency_key=_require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _task_resource(state)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:retry", task_id, task)
        state = await self._kernel.retry_task(
            idempotency_key=_require_key(context),
            task_id=task_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _run_resource(state)

    async def list_runs(
        self,
        context: RequestContext,
        query: PageQuery,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "run:list", task_id or "runs")
        task_ids = (task_id,) if task_id is not None else await self._task_ids()
        resources: list[dict[str, JsonValue]] = []
        for current_task_id in task_ids:
            task = await self._kernel.get_task(current_task_id)
            for run_id in task.run_ids:
                if await self._allowed_for_task(context, "run:list", run_id, task):
                    resources.append(
                        _run_resource(await self._kernel.get_run(current_task_id, run_id))
                    )
        return paginate(resources, query)

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        if task_id is not None:
            task = await self._kernel.get_task(task_id)
            run = await self._kernel.get_run(task_id, run_id)
            await self._authorize_for_task(context, "run:read", run_id, task)
            return _run_resource(run)
        for current_task_id in await self._task_ids():
            task = await self._kernel.get_task(current_task_id)
            if run_id in task.run_ids:
                run = await self._kernel.get_run(current_task_id, run_id)
                await self._authorize_for_task(context, "run:read", run_id, task)
                return _run_resource(run)
        raise ContractError(ErrorCode.NOT_FOUND, f"run not found: {run_id}")

    async def cancel_run(
        self,
        context: RequestContext,
        task_id: str,
        run_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._kernel.get_run(task_id, run_id)
        await self._authorize_for_task(context, "run:cancel", run_id, task)
        state = await self._kernel.cancel_run(
            idempotency_key=_require_key(context),
            task_id=task_id,
            run_id=run_id,
            actor_ref=context.actor.principal_ref,
            source="control-plane",
        )
        return _run_resource(state)

    async def list_references(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, f"{collection}:list", collection)
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            for resource in _references_for_task(task, collection):
                resource_id = resource["id"]
                if isinstance(resource_id, str) and await self._allowed_for_task(
                    context,
                    f"{collection}:list",
                    resource_id,
                    task,
                ):
                    resources.append(resource)
        return paginate(_deduplicate(resources), query)

    async def get_reference(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            for resource in _references_for_task(task, collection):
                if resource.get("id") != resource_id:
                    continue
                await self._authorize_for_task(
                    context,
                    f"{collection[:-1]}:read",
                    resource_id,
                    task,
                )
                return resource
        raise ContractError(ErrorCode.NOT_FOUND, f"{collection[:-1]} not found: {resource_id}")

    async def timeline(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:list", task_id, task)
        events = [_event_resource(event) for event in await self._events.read_events(task_id)]
        return paginate(events, query)

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:subscribe", task_id, task)

        live_events = self._live_events
        if live_events is not None:

            async def live_iterator() -> AsyncIterator[dict[str, JsonValue]]:
                async for event in live_events.subscribe(
                    task_id,
                    after_event_id=after_event_id,
                    control=OperationControl(),
                ):
                    yield _event_resource(event)

            return live_iterator()

        events = await self._events.read_events(task_id)
        start_index = 0
        if after_event_id is not None:
            for index, event in enumerate(events):
                if event.id == after_event_id:
                    start_index = index + 1
                    break
            else:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"event cursor not found: {after_event_id}",
                )

        async def repository_iterator() -> AsyncIterator[dict[str, JsonValue]]:
            for event in events[start_index:]:
                yield _event_resource(event)

        return repository_iterator()

    async def list_model_providers(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:list", "model-providers")
        resources = [
            _model_provider_resource(self._require_model_registry(), provider)
            for provider in self._require_model_registry().list_providers()
        ]
        return paginate(resources, query)

    async def get_model_provider(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:read", provider_id)
        provider = self._require_model_registry().get_provider(provider_id)
        return _model_provider_resource(self._require_model_registry(), provider)

    async def set_model_provider_enabled(
        self,
        context: RequestContext,
        provider_id: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model-provider:{action}", provider_id)
        self._require_model_registry().set_provider_enabled(provider_id, enabled)
        provider = self._require_model_registry().get_provider(provider_id)
        return _model_provider_resource(self._require_model_registry(), provider)

    async def refresh_model_provider_health(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        await self._authorize(context, "model-provider:refresh-health", provider_id)
        await self._require_model_registry().refresh_health(provider_id)
        provider = self._require_model_registry().get_provider(provider_id)
        return _model_provider_resource(self._require_model_registry(), provider)

    async def list_models(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:list", "models")
        resources = [
            _model_resource(self._require_model_registry(), config)
            for config in self._require_model_registry().list_models()
        ]
        return paginate(resources, query)

    async def get_model(
        self,
        context: RequestContext,
        model_id_or_alias: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:read", model_id_or_alias)
        config = self._require_model_registry().get_model(model_id_or_alias)
        return _model_resource(self._require_model_registry(), config)

    async def set_model_enabled(
        self,
        context: RequestContext,
        model_id_or_alias: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        _require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model:{action}", model_id_or_alias)
        config = self._require_model_registry().set_enabled(model_id_or_alias, enabled)
        return _model_resource(self._require_model_registry(), config)

    def _require_model_registry(self) -> ModelRegistry:
        if self._model_registry is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical model registry is not configured",
                retryable=True,
                details={"resource": "models"},
            )
        return self._model_registry

    async def _task_ids(self) -> tuple[str, ...]:
        return tuple(
            stream_id
            for stream_id in await self._events.list_stream_ids()
            if stream_id.startswith("task_")
        )

    async def _authorize_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> None:
        await self._authorize(
            context,
            action,
            resource_ref,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )

    async def _allowed_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> bool:
        return await self._allowed(
            context,
            action,
            resource_ref,
            owner_type=task.task.owner_ref.type,
            owner_id=task.task.owner_ref.id,
            project_id=task.task.project_id,
        )

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
    ) -> None:
        decision = await self._authorization_decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        if decision is not None and not decision.allowed:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                decision.reason or "operation is forbidden",
            )

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool:
        decision = await self._authorization_decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        return decision is None or decision.allowed

    async def _authorization_decision(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> AuthorizationDecision | None:
        if self._authorization is None:
            return None
        effective_owner_type = owner_type if owner_type is not None else context.actor.owner_type
        effective_owner_id = owner_id if owner_id is not None else context.actor.owner_id
        return await self._authorization.authorize(
            AuthorizationRequest(
                principal_ref=context.actor.principal_ref,
                action=action,
                resource_ref=resource_ref,
                context=OperationContext(
                    correlation_id=context.correlation_id,
                    owner_type=effective_owner_type,
                    owner_id=effective_owner_id,
                    project_id=project_id,
                    control=OperationControl(idempotency_key=context.idempotency_key),
                ),
                request_payload_digest=request_payload_digest,
            )
        )


def _payload_digest(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_owner(
    actor: ActorContext,
    payload: dict[str, JsonValue],
) -> tuple[OwnerType, str]:
    raw_type = _optional_string(payload, "owner_type") or actor.owner_type
    owner_id = _optional_string(payload, "owner_id") or actor.owner_id
    if raw_type is None or owner_id is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "owner_type and owner_id are required until authentication supplies an owner context",
            details={"fields": ["owner_type", "owner_id"]},
        )
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported owner type: {raw_type}",
            details={"field": "owner_type"},
        )
    return cast(OwnerType, raw_type), owner_id


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def _require_key(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Idempotency-Key is required for mutating commands",
            details={"header": "Idempotency-Key"},
        )
    return context.idempotency_key


def _project_resource(project: Project) -> dict[str, JsonValue]:
    return {
        "id": project.id,
        "type": "project",
        "name": project.name,
        "owner": {"type": project.owner_ref.type, "id": project.owner_ref.id},
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
    }


def _workspace_resource(workspace: WorkspaceIdentity) -> dict[str, JsonValue]:
    return {
        "id": workspace.id,
        "type": "workspace",
        "project_id": workspace.project_id,
        "owner": {"type": workspace.owner_type, "id": workspace.owner_id},
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        "lifecycle": "identity_only",
    }


def _task_resource(state: TaskState) -> dict[str, JsonValue]:
    task = state.task
    return {
        "id": state.task_id,
        "type": "task",
        "title": task.title,
        "objective": task.description,
        "status": state.status.value,
        "owner": {"type": task.owner_ref.type, "id": task.owner_ref.id},
        "project_id": task.project_id,
        "revision": state.revision,
        "plan_ref": state.plan_ref,
        "step_ids": list(state.step_ids),
        "run_ids": list(state.run_ids),
        "artifact_ids": list(state.artifact_ids),
        "result_ids": list(state.result_ids),
        "wait_reason": state.wait_reason,
        "blocked": state.blocked,
        "correlation_id": task.correlation_id,
        "causation_id": task.causation_id,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _run_resource(state: RunState) -> dict[str, JsonValue]:
    run = state.run
    return {
        "id": state.run_id,
        "type": "run",
        "task_id": state.task_id,
        "subject_type": run.subject_type,
        "subject_id": run.subject_id,
        "attempt": state.attempt,
        "status": state.status.value,
        "project_id": run.project_id,
        "correlation_id": run.correlation_id,
        "causation_id": run.causation_id,
        "trace_id": run.trace_id,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "output": dict(state.output),
        "artifact_ids": list(state.artifact_ids),
        "result_ids": list(state.result_ids),
        "recovery_required": state.recovery_required,
        "recovery_reason": state.recovery_reason,
    }


def _references_for_task(
    task: TaskState,
    collection: ReferenceCollection,
) -> list[dict[str, JsonValue]]:
    task_id = task.task_id
    if collection == "plans":
        if task.plan_ref is None:
            return []
        return [
            {
                "id": task.plan_ref,
                "type": "plan",
                "task_id": task_id,
                "step_ids": list(task.step_ids),
            }
        ]
    if collection == "steps":
        return [
            {
                "id": step_id,
                "type": "step",
                "task_id": task_id,
                "plan_id": task.plan_ref,
            }
            for step_id in task.step_ids
        ]
    if collection == "artifacts":
        return [
            {
                "id": artifact_id,
                "type": "artifact",
                "task_id": task_id,
            }
            for artifact_id in task.artifact_ids
        ]
    return [
        {
            "id": result_id,
            "type": "result",
            "task_id": task_id,
        }
        for result_id in task.result_ids
    ]


def _model_provider_resource(
    registry: ModelRegistry,
    provider: ModelProvider,
) -> dict[str, JsonValue]:
    descriptor = provider.descriptor
    return {
        "id": descriptor.provider_id,
        "type": "model-provider",
        "provider_type": descriptor.provider_type,
        "contract_version": descriptor.contract_version,
        "supported_operations": list(descriptor.supported_operations),
        "capabilities": [json_object(item) for item in descriptor.capabilities],
        "health": registry.provider_health(descriptor.provider_id).value,
        "enabled": registry.provider_enabled(descriptor.provider_id),
        "available": descriptor.available,
        "limits": dict(descriptor.limits),
        "resources": dict(descriptor.resources),
        "adapter_metadata": [json_object(item) for item in descriptor.adapter_metadata],
    }


def _model_resource(
    registry: ModelRegistry,
    config: ModelConfiguration,
) -> dict[str, JsonValue]:
    resource = json_object(config)
    resource["id"] = config.config_id
    resource["type"] = "model"
    resource["effective_health"] = registry.effective_health(config).value
    return resource


def _event_resource(event: object) -> dict[str, JsonValue]:
    resource = json_object(event)
    resource["type"] = "event"
    return resource


def _deduplicate(items: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    by_id: dict[str, dict[str, JsonValue]] = {}
    for item in items:
        resource_id = item.get("id")
        if isinstance(resource_id, str):
            by_id[resource_id] = item
    return list(by_id.values())
