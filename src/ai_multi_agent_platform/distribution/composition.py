"""Deployment-owned composition helpers for the optional Registry domain."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ai_multi_agent_platform.control_plane.models import RequestContext

from .state import RegistryInstallationStore
from .validation import ValidationContext

Inventory = Callable[[], Iterable[str]]
PermissionInventory = Callable[[RequestContext], Iterable[str]]


class PlatformRegistryValidationContextResolver:
    """Resolve Registry validation inputs from authoritative server-side inventories."""

    def __init__(
        self,
        *,
        platform_version: str,
        installations: RegistryInstallationStore | None = None,
        capabilities: Inventory = lambda: (),
        plugins: Inventory = lambda: (),
        connectors: Inventory = lambda: (),
        models: Inventory = lambda: (),
        grantable_permissions: PermissionInventory = lambda _context: (),
    ) -> None:
        self._platform_version = platform_version
        self._installations = installations
        self._capabilities = capabilities
        self._plugins = plugins
        self._connectors = connectors
        self._models = models
        self._grantable_permissions = grantable_permissions

    async def resolve(self, context: RequestContext) -> ValidationContext:
        installed_items = (
            tuple(record.as_installed() for record in self._installations.list())
            if self._installations is not None
            else ()
        )
        return ValidationContext(
            platform_version=self._platform_version,
            installed_items=installed_items,
            available_capabilities=frozenset(self._capabilities()),
            installed_plugins=frozenset(self._plugins()),
            installed_connectors=frozenset(self._connectors()),
            available_models=frozenset(self._models()),
            grantable_permissions=frozenset(self._grantable_permissions(context)),
        )
