"""Framework-independent stable façade for the v1 Control Plane."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, JsonValue, OperationControl
from ai_multi_agent_platform.domain import Project
from ai_multi_agent_platform.kernel import PlatformKernel, RunState, TaskState
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry

from .authorization_service import ControlPlaneAuthorization
from .health import ControlPlaneHealth
from .model_registry_service import ControlPlaneModelRegistry
from .models import ActorContext, OwnerType, PageQuery, RequestContext, WorkspaceIdentity, paginate
from .request_validation import (
    optional_string,
    require_key,
    required_string,
    resolve_owner,
)
from .resources import (
    ReferenceCollection,
    deduplicate,
    event_resource,
    project_resource,
    references_for_task,
    run_resource,
    task_resource,
    workspace_resource,
)
from .scope_store import ScopeStore
from .task_run_service import ControlPlaneTaskRunService


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
        self._authorization = ControlPlaneAuthorization(authorization)
        self._live_events = live_events
        self._health = ControlPlaneHealth(health_providers)
        self._models = ControlPlaneModelRegistry(model_registry)
        self._task_runs = ControlPlaneTaskRunService(
            kernel=kernel,
            events=events,
            scopes=self._scopes,
            authorization=self._authorization,
        )

    @property
    def scopes(self) -> ScopeStore:
        return self._scopes

    async def health(self) -> dict[str, JsonValue]:
        return await self._health.health()

    async def create_project(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        owner_type, owner_id = resolve_owner(context.actor, payload)
        await self._authorize(
            context,
            "project:create",
            "projects",
            owner_type=owner_type,
            owner_id=owner_id,
            request_payload_digest=ControlPlaneAuthorization.payload_digest(payload),
        )
        project = self._scopes.create_project(
            key=require_key(context),
            name=required_string(payload, "name"),
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=optional_string(payload, "project_id"),
        )
        return project_resource(project)

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
                resources.append(project_resource(project))
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
        return project_resource(project)

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        project_id = required_string(payload, "project_id")
        project = self._scopes.get_project(project_id)
        await self._authorize(
            context,
            "workspace:create",
            project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
            project_id=project.id,
            request_payload_digest=ControlPlaneAuthorization.payload_digest(payload),
        )
        workspace = self._scopes.create_workspace(
            key=require_key(context),
            project_id=project_id,
            workspace_id=optional_string(payload, "workspace_id"),
        )
        return workspace_resource(workspace)

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
                resources.append(workspace_resource(workspace))
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
        return workspace_resource(workspace)

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._task_runs.create_task(context, payload)

    async def _create_task_with_authorization_payload(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
        *,
        authorization_payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Create a Task while binding approval to the original northbound payload."""

        return await self._task_runs.create_task(
            context,
            payload,
            authorization_payload=authorization_payload,
        )

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.list_tasks(context, query)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.get_task(context, task_id)

    async def queue_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.queue_task(context, task_id)

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.start_task(context, task_id)

    async def cancel_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.cancel_task(context, task_id)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.retry_task(context, task_id)

    async def list_runs(
        self,
        context: RequestContext,
        query: PageQuery,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.list_runs(context, query, task_id=task_id)

    async def get_run(
        self,
        context: RequestContext,
        run_id: str,
        *,
        task_id: str | None = None,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.get_run(context, run_id, task_id=task_id)

    async def cancel_run(
        self,
        context: RequestContext,
        task_id: str,
        run_id: str,
    ) -> dict[str, JsonValue]:
        return await self._task_runs.cancel_run(context, task_id, run_id)

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
            for resource in references_for_task(task, collection):
                resource_id = resource["id"]
                if isinstance(resource_id, str) and await self._allowed_for_task(
                    context,
                    f"{collection}:list",
                    resource_id,
                    task,
                ):
                    resources.append(resource)
        return paginate(deduplicate(resources), query)

    async def get_reference(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            for resource in references_for_task(task, collection):
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
        events = [event_resource(event) for event in await self._events.read_events(task_id)]
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
                    yield event_resource(event)

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
                yield event_resource(event)

        return repository_iterator()

    async def list_model_providers(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:list", "model-providers")
        return paginate(self._models.list_providers(), query)

    async def get_model_provider(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model-provider:read", provider_id)
        return self._models.get_provider(provider_id)

    async def set_model_provider_enabled(
        self,
        context: RequestContext,
        provider_id: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model-provider:{action}", provider_id)
        return self._models.set_provider_enabled(provider_id, enabled)

    async def refresh_model_provider_health(
        self,
        context: RequestContext,
        provider_id: str,
    ) -> dict[str, JsonValue]:
        require_key(context)
        await self._authorize(context, "model-provider:refresh-health", provider_id)
        return await self._models.refresh_provider_health(provider_id)

    async def list_models(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:list", "models")
        return paginate(self._models.list_models(), query)

    async def get_model(
        self,
        context: RequestContext,
        model_id_or_alias: str,
    ) -> dict[str, JsonValue]:
        await self._authorize(context, "model:read", model_id_or_alias)
        return self._models.get_model(model_id_or_alias)

    async def set_model_enabled(
        self,
        context: RequestContext,
        model_id_or_alias: str,
        *,
        enabled: bool,
    ) -> dict[str, JsonValue]:
        require_key(context)
        action = "enable" if enabled else "disable"
        await self._authorize(context, f"model:{action}", model_id_or_alias)
        return self._models.set_model_enabled(model_id_or_alias, enabled)

    async def _task_ids(self) -> tuple[str, ...]:
        return await self._task_runs.task_ids()

    async def _authorize_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> None:
        await self._authorization.authorize_for_task(context, action, resource_ref, task)

    async def _allowed_for_task(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        task: TaskState,
    ) -> bool:
        return await self._authorization.allowed_for_task(context, action, resource_ref, task)

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
        await self._authorization.authorize(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
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
        return await self._authorization.allowed(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )

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
        return await self._authorization.decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )


# Private compatibility seams retained for existing internal callers/tests.
def _payload_digest(payload: dict[str, JsonValue]) -> str:
    return ControlPlaneAuthorization.payload_digest(payload)


def _resolve_owner(
    actor: ActorContext,
    payload: dict[str, JsonValue],
) -> tuple[OwnerType, str]:
    return resolve_owner(actor, payload)


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    return required_string(payload, name)


def _optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    return optional_string(payload, name)


def _require_key(context: RequestContext) -> str:
    return require_key(context)


def _project_resource(project: Project) -> dict[str, JsonValue]:
    return project_resource(project)


def _workspace_resource(workspace: WorkspaceIdentity) -> dict[str, JsonValue]:
    return workspace_resource(workspace)


def _task_resource(state: TaskState) -> dict[str, JsonValue]:
    return task_resource(state)


def _run_resource(state: RunState) -> dict[str, JsonValue]:
    return run_resource(state)


def _references_for_task(
    task: TaskState,
    collection: ReferenceCollection,
) -> list[dict[str, JsonValue]]:
    return references_for_task(task, collection)


def _event_resource(event: object) -> dict[str, JsonValue]:
    return event_resource(event)


def _deduplicate(items: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return deduplicate(items)
