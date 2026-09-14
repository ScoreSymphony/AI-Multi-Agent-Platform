"""Module-owned Workspace/Task-management composition for issue #982.

The lower-level linear composition preserves the established Workspace, Run and Task
behavior without the historical diamond MRO. This layer makes Task-management command
and OpenAPI ownership explicit through the canonical ``ControlPlaneModule`` registry
and provides the one registry-aware command dispatch boundary used by later modules.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import ControlPlaneModule, _reject_private_payload, _validate_command_name
from .models import RequestContext
from .module_registry import install_control_plane_modules
from .service import _payload_digest
from .task_management_api import (
    _add_task_management_paths,
    _add_task_management_query_contract,
)
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_UPDATE_COMMAND,
)
from .task_management_contract import (
    _augment_openapi as _augment_task_management_openapi,
)
from .workspace_task_management_explicit_composition import (
    INSECURE_CONTROL_PLANE_ENV,
    ControlPlaneHTTP,
    build_openapi,
)
from .workspace_task_management_explicit_composition import (
    ControlPlane as _LinearControlPlane,
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
    """Defer authorization to the exact-payload Task-management handlers."""

    del context, resource_ref, payload


class ControlPlane(_LinearControlPlane):
    """Canonical registry-aware composition for later Control Plane domains."""

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
                    discover_as_extension=False,
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
        """Dispatch registered commands through one explicit ownership boundary.

        The historical authorization-hardening layer predates module-owned authorizers
        and observers. Registered commands are therefore handled here so every later
        module gets the same deterministic dispatch semantics while the legacy fallback
        remains available to focused lower-level compatibility tests.
        """

        _validate_command_name(command)
        handler = self._command_handlers.get(command)
        if handler is None:
            return await super().execute_command(context, command, resource_ref, payload)
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )

        effective_payload = payload or {}
        authorizer = self._command_authorizers.get(command)
        if authorizer is None:
            await self._authorize(
                context,
                command,
                resource_ref,
                request_payload_digest=_payload_digest(effective_payload),
            )
        else:
            await authorizer(context, resource_ref, effective_payload)

        result = await handler(context, resource_ref, effective_payload)
        _reject_private_payload(result)
        normalized = result
        for _, observer in self._command_observers:
            await observer(context, command, resource_ref, normalized)
        return normalized

    async def _execute_task_management_update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._update_management_command(
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
        return await self._bulk_update_management_command(
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
