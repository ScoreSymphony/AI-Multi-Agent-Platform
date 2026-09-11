"""Project/Workspace northbound operations behind the stable Control Plane façade."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .authorization_service import ControlPlaneAuthorization
from .models import PageQuery, RequestContext, paginate
from .request_validation import optional_string, require_key, required_string, resolve_owner
from .resources import project_resource, workspace_resource
from .scope_store import ScopeStore


class ControlPlaneScopeService:
    """Own Project/Workspace API mechanics while ScopeStore owns identity state."""

    def __init__(
        self,
        *,
        scopes: ScopeStore,
        authorization: ControlPlaneAuthorization,
    ) -> None:
        self._scopes = scopes
        self._authorization = authorization

    async def create_project(
        self,
        context: RequestContext,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        owner_type, owner_id = resolve_owner(context.actor, payload)
        await self._authorization.authorize(
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
        await self._authorization.authorize(context, "project:list", "projects")
        resources: list[dict[str, JsonValue]] = []
        for project in self._scopes.list_projects():
            if await self._authorization.allowed(
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
        await self._authorization.authorize(
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
        await self._authorization.authorize(
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
        await self._authorization.authorize(context, "workspace:list", "workspaces")
        resources: list[dict[str, JsonValue]] = []
        for workspace in self._scopes.list_workspaces():
            if await self._authorization.allowed(
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
        await self._authorization.authorize(
            context,
            "workspace:read",
            workspace_id,
            owner_type=workspace.owner_type,
            owner_id=workspace.owner_id,
            project_id=workspace.project_id,
        )
        return workspace_resource(workspace)
