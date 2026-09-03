"""Public Control Plane composition for canonical Task management.

Built-in Task-management commands are intentionally kept separate from the
future-extension command registry introduced by issue #32. They share the
same HTTP command transport, authorization boundary and idempotency rules,
but do not make the extension registry claim built-in platform behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
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

from .extensions import CommandHandler, ResourceService
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, RequestContext
from .observability_contract import build_openapi as _build_observability_openapi
from .service import ScopeStore
from .task_management_contract import (
    TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
    TASK_MANAGEMENT_COMMANDS,
    TASK_MANAGEMENT_UPDATE_COMMAND,
    _augment_openapi,
)
from .task_management_contract import (
    ControlPlane as _TaskManagementControlPlane,
)
from .task_management_contract import (
    ControlPlaneHTTP as _TaskManagementControlPlaneHTTP,
)


class ControlPlane(_TaskManagementControlPlane):
    """Task-management Control Plane with built-ins separated from extensions."""

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
            task_management=task_management,
        )
        # The parent implementation originally used the generic extension
        # registry as a transport hook. Remove only the platform-owned commands
        # so `registered_commands` keeps its issue-#32 meaning: external/future
        # extension registrations supplied by composition.
        self._command_handlers.pop(TASK_MANAGEMENT_UPDATE_COMMAND, None)
        self._command_handlers.pop(TASK_MANAGEMENT_BULK_UPDATE_COMMAND, None)

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if command not in TASK_MANAGEMENT_COMMANDS:
            return await super().execute_command(context, command, resource_ref, payload)
        if context.idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for mutating commands",
                details={"header": "Idempotency-Key"},
            )
        await self._authorize(context, command, resource_ref)
        body = payload or {}
        if command == TASK_MANAGEMENT_UPDATE_COMMAND:
            return await self._update_management_command(context, resource_ref, body)
        return await self._bulk_update_management_command(context, resource_ref, body)


class ControlPlaneHTTP(_TaskManagementControlPlaneHTTP):
    """HTTP transport exposing explicit OpenAPI paths for Task-management commands."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = deepcopy(response.body)
            _add_task_management_paths(specification)
            return HTTPResponse(
                status=response.status,
                body=specification,
                headers=dict(response.headers),
            )
        return response


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build OpenAPI with native #88 commands separate from extension discovery."""

    specification = _augment_openapi(
        _build_observability_openapi(
            extension_collections=extension_collections,
            extension_commands=extension_commands,
        )
    )
    _add_task_management_paths(specification)
    return specification


def _add_task_management_paths(specification: dict[str, Any]) -> None:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return
    for command, operation_id, summary in (
        (
            TASK_MANAGEMENT_UPDATE_COMMAND,
            "updateTaskManagement",
            "Update canonical Task planning metadata",
        ),
        (
            TASK_MANAGEMENT_BULK_UPDATE_COMMAND,
            "bulkUpdateTaskManagement",
            "Bulk update canonical Task planning metadata",
        ),
    ):
        paths[f"/api/{API_VERSION}/commands/{command}"] = {
            "post": {
                "operationId": operation_id,
                "summary": summary,
                "parameters": [
                    {
                        "name": "Idempotency-Key",
                        "in": "header",
                        "required": True,
                        "schema": {"type": "string", "minLength": 1},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["resource_ref"],
                                "properties": {"resource_ref": {"type": "string", "minLength": 1}},
                                "additionalProperties": True,
                            }
                        }
                    },
                },
                "responses": {
                    "200": {"description": "Canonical Task-management command result"},
                    "400": {"description": "Invalid request"},
                    "403": {"description": "Forbidden"},
                    "409": {"description": "Conflict"},
                },
            }
        }
