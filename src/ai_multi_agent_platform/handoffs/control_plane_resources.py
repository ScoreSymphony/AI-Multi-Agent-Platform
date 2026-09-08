"""Canonical read-only Control Plane resource registration for Agent Handoffs (#651)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .control_plane import HandoffControlPlaneProjection

HANDOFF_COLLECTION = "agent-handoffs"
HANDOFF_CONSUMPTION_COLLECTION = "agent-handoff-consumptions"


@dataclass(slots=True)
class HandoffResourceService:
    projection: HandoffControlPlaneProjection

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return tuple(self.projection.list_visible_handoffs(context.actor.principal_ref))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return self.projection.get_handoff(context.actor.principal_ref, resource_id)


@dataclass(slots=True)
class HandoffConsumptionResourceService:
    projection: HandoffControlPlaneProjection

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del query
        return tuple(self.projection.list_visible_consumptions(context.actor.principal_ref))

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        return self.projection.get_consumption(context.actor.principal_ref, resource_id)


def register_handoff_control_plane(
    control_plane: ControlPlane,
    projection: HandoffControlPlaneProjection,
) -> None:
    """Register only read resources; Handoff creation/consumption remains runtime-owned."""

    control_plane.register_resource_service(
        HANDOFF_COLLECTION,
        HandoffResourceService(projection),
    )
    control_plane.register_resource_service(
        HANDOFF_CONSUMPTION_COLLECTION,
        HandoffConsumptionResourceService(projection),
    )
