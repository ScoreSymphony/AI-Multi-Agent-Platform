"""Final Control Plane composition for canonical Workspaces and Task management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

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
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    WorkspaceProvider,
)

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext
from .run_workspace_contract import ControlPlane as _RunWorkspaceControlPlane
from .run_workspace_contract import ControlPlaneHTTP as _RunWorkspaceControlPlaneHTTP
from .run_workspace_contract import _augment_run_workspace_openapi
from .service import ScopeStore
from .task_management_api import ControlPlane as _TaskManagementControlPlane
from .task_management_api import ControlPlaneHTTP as _TaskManagementControlPlaneHTTP
from .task_management_api import build_openapi as _build_task_management_openapi
from .workspace_contract import _augment_workspace_openapi


class ControlPlane(_RunWorkspaceControlPlane, _TaskManagementControlPlane):
    """Compose #37 Workspace semantics with the current #88 Task-management API."""

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
            workspace_provider=workspace_provider,
            run_workspace_bindings=run_workspace_bindings,
        )
        if task_management is not None:
            self._task_management = task_management
        elif workspace_provider is not None:
            self._task_management = TaskManagementService(
                kernel=kernel,
                workspace_project_resolver=self._workspace_provider_project_id,
            )

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        """Keep archived/hidden Tasks out of the default product queue."""

        filters = dict(query.filters or {})
        filters.setdefault("archived", "false")
        filters.setdefault("hidden", "false")
        visible_query = PageQuery(
            limit=query.limit,
            cursor=query.cursor,
            sort=query.sort,
            direction=query.direction,
            search=query.search,
            filters=filters,
            fields=query.fields,
        )
        return await super().list_tasks(context, visible_query)

    async def _workspace_provider_project_id(self, workspace_id: str) -> str:
        provider = self.workspace_provider
        if provider is None:
            return self._workspace_project_id(workspace_id)
        workspace = await provider.get_workspace(workspace_id)
        return workspace.project_id


class ControlPlaneHTTP(_RunWorkspaceControlPlaneHTTP, _TaskManagementControlPlaneHTTP):
    """HTTP composition preserving both Workspace and Task-management routes."""


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    specification = _build_task_management_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    specification = _augment_workspace_openapi(specification)
    return _augment_run_workspace_openapi(specification)
