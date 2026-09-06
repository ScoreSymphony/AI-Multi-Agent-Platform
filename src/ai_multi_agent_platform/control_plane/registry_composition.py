"""Compose Registry discovery without double-applying provider-specific filters."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distribution.control_plane import REGISTRY_COLLECTION

from .approval_portability_composition import ControlPlane as _BaseControlPlane
from .extensions import _singular, _validate_resources
from .models import PageQuery, RequestContext, paginate


class ControlPlane(_BaseControlPlane):
    """Current Single-Node Control Plane with canonical Registry list semantics."""

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        if collection != REGISTRY_COLLECTION:
            return await super().list_extension_resources(context, collection, query)

        service = self._registered_resource_service(collection)
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
