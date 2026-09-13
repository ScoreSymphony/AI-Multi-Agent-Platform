"""Resource-scoped authorization helpers for canonical Template integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from ai_multi_agent_platform.control_plane.async_scope import AsyncScopeStore
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef, Project


class _ScopedControlPlane(Protocol):
    @property
    def runtime_scopes(self) -> AsyncScopeStore: ...

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
    ) -> None: ...

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
    ) -> bool: ...


@dataclass(slots=True)
class TemplateScopeAccess:
    """Reuse composed Control Plane authorization and awaitable canonical Scope access."""

    control_plane: ControlPlane

    async def get_project(self, project_id: str) -> Project:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        return await scoped.runtime_scopes.get_project(project_id)

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        await scoped._authorize(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        return await scoped._allowed(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
            request_payload_digest=request_payload_digest,
        )
