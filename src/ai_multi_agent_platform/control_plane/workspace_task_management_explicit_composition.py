"""Explicit linear composition for Workspace, Run bindings and Task management (#982).

The historical final façade combined independent Workspace/Run and Task-management
ControlPlane branches through Python MRO.  This composition keeps Run/Workspace as the
single domain base, owns Task-management state through ``TaskManagementService`` and
reuses the existing Task-management adapter methods without a second ControlPlane base.
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, cast

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    normalize_authorization_decision,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.task_management import TaskManagementService

from .authorization_hardening import AuthorizationBoundaryHardeningMixin
from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext
from .repository_run_provenance import RepositoryRunProvenanceMixin
from .run_workspace_contract import ControlPlane as _RunWorkspaceControlPlane
from .run_workspace_contract import ControlPlaneHTTP as _RunWorkspaceControlPlaneHTTP
from .run_workspace_contract import _augment_run_workspace_openapi
from .task_management_api import (
    _add_task_management_paths,
    _add_task_management_query_contract,
    _filter_custom_queue_state,
    _task_page_query,
)
from .task_management_api import build_openapi as _build_task_management_openapi
from .task_management_contract import ControlPlane as _TaskManagementAdapter
from .task_management_contract import (
    _augment_openapi as _augment_task_management_openapi,
)
from .workspace_contract import _augment_workspace_openapi

INSECURE_CONTROL_PLANE_ENV = "AI_MULTI_AGENT_PLATFORM_ALLOW_INSECURE_CONTROL_PLANE"


class ControlPlane(
    RepositoryRunProvenanceMixin,
    AuthorizationBoundaryHardeningMixin,
    _RunWorkspaceControlPlane,
):
    """Secure Workspace/Run façade with Task management composed as a service adapter."""

    def __init__(
        self,
        *args: Any,
        task_management: TaskManagementService | None = None,
        **kwargs: Any,
    ) -> None:
        authorization = kwargs.get("authorization")
        if authorization is None and os.environ.get(INSECURE_CONTROL_PLANE_ENV) != "1":
            raise ValueError(
                "authorization is required for the composed Control Plane; "
                f"set {INSECURE_CONTROL_PLANE_ENV}=1 only for explicit development/test use"
            )

        super().__init__(*args, **kwargs)
        workspace_provider = self.workspace_provider
        if task_management is not None:
            self._task_management = task_management
        elif workspace_provider is not None:
            self._task_management = TaskManagementService(
                kernel=self._kernel,
                workspace_project_resolver=self._workspace_provider_project_id,
            )
        else:
            self._task_management = TaskManagementService(
                kernel=self._kernel,
                workspace_project_resolver=self._workspace_project_id,
            )

    @property
    def task_management(self) -> TaskManagementService:
        return self._task_management

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
        """Preserve canonical authorization metadata in northbound forbidden errors."""

        decision = await self._authorization_decision(
            context,
            action,
            resource_ref,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
        if decision is None:
            return
        canonical = normalize_authorization_decision(decision)
        if canonical.allowed:
            return

        details: dict[str, JsonValue] = {
            "authorization_outcome": canonical.outcome.value,
        }
        if canonical.policy_id is not None:
            details["policy_id"] = canonical.policy_id
        details.update(dict(canonical.constraints))
        raise ContractError(
            ErrorCode.FORBIDDEN,
            canonical.reason or "operation is forbidden",
            details=details,
        )

    async def create_task(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await self._call_task_management_adapter(
            _TaskManagementAdapter.create_task,
            context,
            payload,
        )

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        """List managed Tasks while hiding archived/hidden work by default."""

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

        await self._authorize(context, "task:list", "tasks")
        resources: list[dict[str, JsonValue]] = []
        for task_id in await self._task_ids():
            task = await self._kernel.get_task(task_id)
            if await self._allowed_for_task(context, "task:list", task_id, task):
                resources.append(await self._managed_task_resource(task))
        ranged = _filter_custom_queue_state(resources, visible_query)
        return self._paginate_task_management(ranged, visible_query)

    async def get_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        return await self._call_task_management_adapter(
            _TaskManagementAdapter.get_task,
            context,
            task_id,
        )

    async def queue_task(
        self,
        context: RequestContext,
        task_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "task:queue", task_id, task)
        await self._task_management.require_eligible(task_id)
        await super().queue_task(context, task_id)
        return await self.get_task(context, task_id)

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        await self._task_management.require_eligible(task_id)
        return await super().start_task(context, task_id, payload)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        await self._task_management.require_eligible(task_id)
        return await super().retry_task(context, task_id, payload)

    async def execute_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        return await super().execute_command(context, command, resource_ref, payload)

    async def _update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await super()._update_management_command(context, resource_ref, payload)

    async def _bulk_update_management_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        return await super()._bulk_update_management_command(context, resource_ref, payload)

    async def _managed_task_resource(self, state: Any) -> dict[str, JsonValue]:
        return await self._call_task_management_adapter(
            _TaskManagementAdapter._managed_task_resource,
            state,
        )

    def _workspace_project_id(self, workspace_id: str) -> str:
        return self.scopes.get_workspace(workspace_id).project_id

    async def _workspace_provider_project_id(self, workspace_id: str) -> str:
        provider = self.workspace_provider
        if provider is None:
            return self._workspace_project_id(workspace_id)
        workspace = await provider.get_workspace(workspace_id)
        return workspace.project_id

    async def _call_task_management_adapter(
        self,
        handler: Any,
        *args: Any,
    ) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], await handler(self, *args))

    @staticmethod
    def _paginate_task_management(
        resources: list[dict[str, JsonValue]],
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        from .models import paginate

        return paginate(resources, _task_page_query(query))


class ControlPlaneHTTP(_RunWorkspaceControlPlaneHTTP):
    """Run/Workspace HTTP plus Task-management schema and command metadata."""

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        response = await super().handle(request)
        if (
            request.method == "GET"
            and request.path.rstrip("/") == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            specification = _augment_task_management_openapi(
                cast(dict[str, Any], deepcopy(response.body))
            )
            _add_task_management_paths(specification)
            _add_task_management_query_contract(specification)
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
    specification = _build_task_management_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    specification = _augment_workspace_openapi(specification)
    return _augment_run_workspace_openapi(specification)


__all__ = [
    "ControlPlane",
    "ControlPlaneHTTP",
    "INSECURE_CONTROL_PLANE_ENV",
    "build_openapi",
]
