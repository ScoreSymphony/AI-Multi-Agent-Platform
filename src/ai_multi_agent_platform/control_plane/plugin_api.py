"""Canonical northbound plugin lifecycle compatibility facade.

Plugin lifecycle behavior is owned by ``PluginControlPlaneBinding`` and exposed by an
explicit ``ControlPlaneModule``.  This class remains only as a stable construction
surface for existing imports.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.plugins import PluginCatalog, PluginRegistry

from .authenticated_authorization import ControlPlane as _CurrentControlPlane
from .module_registry import install_control_plane_modules
from .plugin_module import (
    PLUGIN_CANDIDATE_COLLECTION,
    PLUGIN_COLLECTION,
    PLUGIN_COLLECTIONS,
    PLUGIN_COMMANDS,
    PLUGIN_MODULE,
    PluginControlPlaneBinding,
    PluginPermissionResolver,
    _manifest_document,
)


class ControlPlane(_CurrentControlPlane):
    """Compatibility facade that installs the explicit plugin lifecycle module."""

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
        """Attach the optional plugin lifecycle exactly once through module registration."""

        module = self._plugin_binding.attach(
            plugin_registry,
            plugin_catalog=plugin_catalog,
            plugin_permission_resolver=plugin_permission_resolver,
        )
        install_control_plane_modules(self, (module,))


__all__ = [
    "PLUGIN_CANDIDATE_COLLECTION",
    "PLUGIN_COLLECTION",
    "PLUGIN_COLLECTIONS",
    "PLUGIN_COMMANDS",
    "PLUGIN_MODULE",
    "ControlPlane",
    "PluginPermissionResolver",
    "_manifest_document",
]
