"""Module-owned Workspace/Task-management composition for issue #982.

The lower-level linear composition preserves the established Workspace, Run and Task
behavior without the historical diamond MRO. This layer makes Task-management command
and OpenAPI ownership explicit through the canonical ``ControlPlaneModule`` registry
while preserving the exact-payload authorization semantics already enforced by the
hardened composition.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts.types import JsonValue

from .authorization_hardening import AuthorizationBoundaryHardeningMixin
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
    TASK_MANAGEMENT_COMMANDS,
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


async def _task_management_authorizer(
    context: RequestContext,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> None:
    """Defer authorization to the exact-payload Task-management handlers.

    The hardening mixin authorizes individual Task relationships and includes the
    request payload digest. Running the generic command-name preflight first would
    change that established #15 contract, so the module declares this exception
    explicitly rather than relying on subclass dispatch order.
    """

    del context, resource_ref, payload


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
                        TASK_MANAGEMENT_UPDATE_COMMAND: self._execute_task_management_update,
                        TASK_MANAGEMENT_BULK_UPDATE_COMMAND: (
                            self._execute_task_management_bulk_update
                        ),
                    },
                    command_authorizers={
                        TASK_MANAGEMENT_UPDATE_COMMAND: _task_management_authorizer,
                        TASK_MANAGEMENT_BULK_UPDATE_COMMAND: _task_management_authorizer,
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
        """Use explicit ownership for Task management and preserve earlier domain dispatch."""

        if command in TASK_MANAGEMENT_COMMANDS:
            return await _RegistryControlPlane.execute_command(
                self,
                context,
                command,
                resource_ref,
                payload,
            )
        return await super().execute_command(context, command, resource_ref, payload)

    async def _execute_task_management_update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await AuthorizationBoundaryHardeningMixin._update_management_command(
            self,
            context,
            resource_ref,
            payload,
        )

    async def _execute_task_management_bulk_update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await AuthorizationBoundaryHardeningMixin._bulk_update_management_command(
            self,
            context,
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
