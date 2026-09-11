"""Framework-independent stable façade for the v1 Control Plane."""

from __future__ import annotations

from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ModelProvider,
    ProviderContract,
)
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, JsonValue
from ai_multi_agent_platform.domain import Project
from ai_multi_agent_platform.kernel import PlatformKernel, RunState, TaskState
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelConfiguration, ModelRegistry

from .authorization_service import ControlPlaneAuthorization
from .health import ControlPlaneHealth
from .model_registry_service import ControlPlaneModelRegistry
from .model_registry_service import (
    _model_provider_resource as model_provider_resource,
)
from .model_registry_service import (
    _model_resource as model_resource,
)
from .models import (
    ActorContext,
    OwnerType,
    PageQuery,
    RequestContext,
    WorkspaceIdentity,
    paginate,
)
from .reference_event_service import ControlPlaneReferenceEventService
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
from .scope_service import ControlPlaneScopeService
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
        self._authorization_service = ControlPlaneAuthorization(authorization)
        self._live_events = live_events
        self._health = ControlPlaneHealth(health_providers)
        self._model_registry = model_registry
        self._models = ControlPlaneModelRegistry(model_registry)
        self._scope_resources = ControlPlaneScopeService(
            scopes=self._scopes,
            authorization=self._authorization_service,
        )
        self._task_runs = ControlPlaneTaskRunService(
            kernel=kernel,
            events=events,
            scopes=self._scopes,
            authorization=self._authorization_service,
        )
        self._reference_events = ControlPlaneReferenceEventService(
            kernel=kernel,
            events=events,
            authorization=self._authorization_service,
            task_runs=self._task_runs,
            live_events=live_events,
        )

    @property
    def _authorization(self) -> AuthorizationProvider | None:
        """Preserve the legacy provider seam used by composed Control Plane layers."""

        return self._authorization_service.provider

    @_authorization.setter
    def _authorization(self, provider: AuthorizationProvider | None) -> None:
        self._authorization_service.provider = provider

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
        return await self._scope_resources.create_project(context, payload)

    async def list_projects(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return await self._scope_resources.list_projects(context, query)

    async def get_project(
        self,
        context: RequestContext,
        project_id: str,
    ) -> dict[str, JsonValue]:
        return await self._scope_resources.get_project(context, project_id)

    async def create_workspace(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._scope_resources.create_workspace(context, payload)

    async def list_workspaces(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return await self._scope_resources.list_workspaces(context, query)

    async def get_workspace(
        self,
        context: RequestContext,
        workspace_id: str,
    ) -> dict[str, JsonValue]:
        return await self._scope_resources.get_workspace(context, workspace_id)

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
        return await self._reference_events.list_references(context, collection, query)

    async def get_reference(
        self,
        context: RequestContext,
        collection: ReferenceCollection,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return await self._reference_events.get_reference(context, collection, resource_id)

    async def timeline(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return await self._reference_events.timeline(context, task_id, query)

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, JsonValue]]:
        return await self._reference_events.subscribe_task_events(
            context,
            task_id,
            after_event_id=after_event_id,
        )

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
        return await self._authorization_service.decision(
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


def _model_provider_resource(
    registry: ModelRegistry,
    provider: ModelProvider,
) -> dict[str, JsonValue]:
    return model_provider_resource(registry, provider)


def _model_resource(
    registry: ModelRegistry,
    config: ModelConfiguration,
) -> dict[str, JsonValue]:
    return model_resource(registry, config)


def _deduplicate(items: list[dict[str, JsonValue]]) -> list[dict[str, JsonValue]]:
    return deduplicate(items)


__all__ = ["ControlPlane", "ReferenceCollection", "ScopeStore"]
