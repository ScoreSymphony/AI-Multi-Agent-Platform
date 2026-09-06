"""Single-node composition joining canonical Approval decisions with portability.

This module is intentionally not exported from ``control_plane.__init__``. Importing the
portability stack while the package root is being initialized would re-enter the Agent
and Template packages and create a circular import. Deployment composition imports this
module only after the Agent package is fully initialized.
"""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .approval_decision_composition import ControlPlane as _ApprovalControlPlane
from .extensions import _singular, _validate_resources
from .models import PageQuery, RequestContext, paginate
from .portability_api import ControlPlane as _PortabilityControlPlane


class ControlPlane(_ApprovalControlPlane, _PortabilityControlPlane):
    """Approval-aware Control Plane that also consumes the portability workflow."""

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        if not bool(getattr(service, "handles_search_and_filters", False)):
            return await super().list_extension_resources(context, collection, query)

        await self._authorize(context, f"{_singular(collection)}:list", collection)
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)
        pagination = PageQuery(
            limit=query.limit,
            cursor=query.cursor,
            sort=query.sort,
            direction=query.direction,
            fields=query.fields,
        )
        return paginate(resources, pagination)


__all__ = ["ControlPlane"]
