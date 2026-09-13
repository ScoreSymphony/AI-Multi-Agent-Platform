"""Module-owned Workspace/Task-management composition for issue #982.

The lower-level linear composition preserves the established Workspace, Run and Task
behavior without the historical diamond MRO.  This layer makes Task-management command
and OpenAPI ownership explicit through the canonical ``ControlPlaneModule`` registry so
canonical callers do not retain a second command-dispatch path.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import ControlPlane as _RegistryControlPlane
from .extensions import ControlPlaneModule
from .models import RequestContext
from .module_registry import install_control_plane_modules
from .task_management_api import (
    _add_task_management_paths,
    _add_task_management_query_contract,
)
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_UPDATE_COMMAND,
    _augment_openapi as _augment_task_management_openapi,
)
from .workspace_task_management_explicit_composition import (
    INSECURE_CONTROL_PLANE_ENV,
    ControlPlane as _LinearControlPlane,
    ControlPlaneHTTP,
    build_openapi,
)

TASK_MANAGEMENT_MODULE = "task-management"


def _augment_task_management_module_openapi(specification: dict[str, Any]) -> None:
    """Publish Task-management schema/path contributions under one module owner."""

    _augment_task_management_openapi(specification)
    _add_task_management_paths(specification)
    _add_task_management_query_contract(specification)


class ControlPlane(_LinearControlPlane):
    """Canonical linear composition with explicitly owned Task-management commands."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        install_control_plane_modules(
            self,
            (
                ControlPlaneModule(
                    name=TASK_MANAGEMENT_MODULE,
                    command_handlers={
                        TASK_MANAGEMENT_UPDATE_COMMAND: self._update_management_command,
                        TASK_MANAGEMENT_BULK_UPDATE_COMMAND: self._bulk_update_management_command,
                    },
                    openapi_contributors=(_augment_task_management_module_openapi,),
                ),
            ),
        )

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        """Dispatch every canonical command through the explicit ownership registry."""

        return await _RegistryControlPlane.execute_command(
            self,
            context,
            command,
            resource_ref,
            payload,
        )


__all__ = [
    "ControlPlane",
    "ControlPlaneHTTP",
    "INSECURE_CONTROL_PLANE_ENV",
    "TASK_MANAGEMENT_MODULE",
    "build_openapi",
]
