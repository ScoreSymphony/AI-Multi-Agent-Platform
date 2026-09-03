"""Compose optional plugin lifecycle on top of the current terminal Control Plane."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import _singular, _validate_resources
from .models import PageQuery, RequestContext, paginate
from .plugin_api import ControlPlane as _PluginControlPlane
from .terminal_composition import ControlPlane as _TerminalControlPlane
from .terminal_composition import (
    ControlPlaneASGI,
    ControlPlaneHTTP,
    build_openapi,
)


@runtime_checkable
class _AuthorizationScopedResourceService(Protocol):
    """Optional extension hook for resources whose visibility varies per canonical scope."""

    def authorization_scope(
        self,
        resource: Mapping[str, JsonValue],
    ) -> tuple[str | None, str | None, str | None]: ...


class ControlPlane(_PluginControlPlane, _TerminalControlPlane):
    """Current Control Plane with plugin, terminal and scoped-resource composition."""

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        if not isinstance(service, _AuthorizationScopedResourceService):
            return await super().list_extension_resources(context, collection, query)

        action = f"{_singular(collection)}:list"
        await self._authorize(context, action, collection)
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)

        visible: list[dict[str, JsonValue]] = []
        for resource in resources:
            resource_id = resource.get("id")
            if not isinstance(resource_id, str) or not resource_id.strip():
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"canonical {collection} resource requires a non-blank id",
                )
            owner_type, owner_id, project_id = service.authorization_scope(resource)
            if await self._allowed(
                context,
                action,
                resource_id,
                owner_type=owner_type,
                owner_id=owner_id,
                project_id=project_id,
            ):
                visible.append(resource)
        return paginate(visible, query)

    async def get_extension_resource(
        self,
        context: RequestContext,
        collection: str,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        if not isinstance(service, _AuthorizationScopedResourceService):
            return await super().get_extension_resource(context, collection, resource_id)

        action = f"{_singular(collection)}:read"
        await self._authorize(context, action, resource_id)
        resource = await service.get_resource(context, resource_id)
        _validate_resources(collection, [resource])
        owner_type, owner_id, project_id = service.authorization_scope(resource)
        if not await self._allowed(
            context,
            action,
            resource_id,
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        ):
            raise ContractError(ErrorCode.NOT_FOUND, f"resource not found: {resource_id}")
        return resource


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
