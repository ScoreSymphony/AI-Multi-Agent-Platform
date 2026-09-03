"""Final Control Plane composition for canonical Workspaces and Task management."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ai_multi_agent_platform.contracts.interfaces import (
    AuthorizationProvider,
    EventProvider,
    ProviderContract,
)
from ai_multi_agent_platform.kernel import PlatformKernel
from ai_multi_agent_platform.kernel.repository import EventRepository
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.task_management import TaskManagementService
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    WorkspaceProvider,
)

from .authorization_hardening import AuthorizationBoundaryHardeningMixin
from .extensions import CommandHandler, ResourceService
from .run_workspace_contract import ControlPlane as _RunWorkspaceControlPlane
from .run_workspace_contract import ControlPlaneHTTP as _RunWorkspaceControlPlaneHTTP
from .run_workspace_contract import _augment_run_workspace_openapi
from .service import ScopeStore
from .task_management_api import ControlPlane as _TaskManagementControlPlane
from .task_management_api import ControlPlaneHTTP as _TaskManagementControlPlaneHTTP
from .task_management_api import build_openapi as _build_task_management_openapi
from .workspace_contract import _augment_workspace_openapi


class ControlPlane(
    AuthorizationBoundaryHardeningMixin,
    _RunWorkspaceControlPlane,
    _TaskManagementControlPlane,
):
    """Compose #37/#88 semantics while preserving canonical #15 authorization."""

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
