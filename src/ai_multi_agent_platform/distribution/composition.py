"""Deployment-owned composition helpers for the optional Registry domain."""

from __future__ import annotations

import platform as host_platform
from collections.abc import Callable, Iterable

from ai_multi_agent_platform.control_plane.models import RequestContext

from .items import InstalledRegistryItem
from .state import RegistryInstallationStore
from .validation import ValidationContext, merge_installed_items

Inventory = Callable[[], Iterable[str]]
InstalledItemInventory = Callable[[], Iterable[InstalledRegistryItem]]
PermissionInventory = Callable[[RequestContext], Iterable[str]]


class PlatformRegistryValidationContextResolver:
    """Resolve Registry validation inputs from authoritative server-side inventories."""

    def __init__(
        self,
        *,
        platform_version: str,
        installations: RegistryInstallationStore | None = None,
        installed_items: InstalledItemInventory = lambda: (),
        capabilities: Inventory = lambda: (),
        plugins: Inventory = lambda: (),
        connectors: Inventory = lambda: (),
        models: Inventory = lambda: (),
        runtimes: Inventory = lambda: (),
        operating_system: str | None = None,
        architecture: str | None = None,
        grantable_permissions: PermissionInventory = lambda _context: (),
    ) -> None:
        self._platform_version = platform_version
        self._installations = installations
        self._installed_items = installed_items
        self._capabilities = capabilities
        self._plugins = plugins
        self._connectors = connectors
        self._models = models
        self._runtimes = runtimes
        self._operating_system = operating_system or host_platform.system().casefold()
        self._architecture = architecture or host_platform.machine().casefold()
        self._grantable_permissions = grantable_permissions

    async def resolve(self, context: RequestContext) -> ValidationContext:
        owner_items = tuple(self._installed_items())
        marketplace_items = (
            tuple(record.as_installed() for record in self._installations.list())
            if self._installations is not None
            else ()
        )
        return ValidationContext(
            platform_version=self._platform_version,
            installed_items=merge_installed_items(marketplace_items, owner_items),
            available_capabilities=frozenset(self._capabilities()),
            installed_plugins=frozenset(self._plugins()),
            installed_connectors=frozenset(self._connectors()),
            available_models=frozenset(self._models()),
            grantable_permissions=frozenset(self._grantable_permissions(context)),
            operating_system=self._operating_system,
            architecture=self._architecture,
            available_runtimes=frozenset(self._runtimes()),
        )
