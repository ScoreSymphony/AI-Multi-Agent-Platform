"""Compose optional plugin lifecycle with terminal sessions without MRO diamonds."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.plugins import PluginCatalog, PluginRegistry

from .extensions import _singular, _validate_resources
from .models import PageQuery, RequestContext, paginate
from .module_registry import install_control_plane_modules
from .plugin_module import PluginControlPlaneBinding, PluginPermissionResolver
from .terminal_explicit_composition import ControlPlane as _TerminalControlPlane
from .terminal_explicit_composition import (
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


class ControlPlane(_TerminalControlPlane):
    """Terminal composition plus explicitly registered plugin lifecycle."""

    def __init__(
        self,
        *args: Any,
        plugin_registry: PluginRegistry | None = None,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
        **kwargs: Any,
    ) -> None:
        binding = PluginControlPlaneBinding(
            plugin_registry,
            plugin_catalog=plugin_catalog,
            plugin_permission_resolver=plugin_permission_resolver,
        )
        super().__init__(*args, **kwargs)
        self._plugin_binding = binding
        if plugin_registry is not None:
            install_control_plane_modules(self, (binding.module(),))

    @property
    def plugin_registry(self) -> PluginRegistry | None:
        return self._plugin_binding.plugin_registry

    @property
    def plugin_catalog(self) -> PluginCatalog | None:
        return self._plugin_binding.plugin_catalog

    def attach_plugin_runtime(
        self,
        plugin_registry: PluginRegistry,
        *,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
    ) -> None:
        module = self._plugin_binding.attach(
            plugin_registry,
            plugin_catalog=plugin_catalog,
            plugin_permission_resolver=plugin_permission_resolver,
        )
        install_control_plane_modules(self, (module,))

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
            raise ContractError(ErrorCode.NOT_FOUND, "resource not found")
        return resource


__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
