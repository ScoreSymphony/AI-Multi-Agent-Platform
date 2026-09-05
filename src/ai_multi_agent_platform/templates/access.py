"""Resource-scoped authorization helpers for canonical Template integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, cast

from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef


class _ScopedControlPlane(Protocol):
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
    """Reuse the composed Control Plane authorization provider with canonical resource scope."""

    control_plane: ControlPlane

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
    ) -> None:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        await scoped._authorize(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
        )

    async def allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
    ) -> bool:
        scoped = cast(_ScopedControlPlane, self.control_plane)
        return await scoped._allowed(
            context,
            action,
            resource_ref,
            owner_type=owner_ref.type,
            owner_id=owner_ref.id,
            project_id=project_id,
        )
