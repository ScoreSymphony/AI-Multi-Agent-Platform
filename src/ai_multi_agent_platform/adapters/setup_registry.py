"""Concrete Distribution-to-onboarding Registry adapter.

This module is the outer composition boundary where the generic Distribution/Marketplace domain is
projected into the smaller browser-first setup contract.  Onboarding therefore never imports the
Distribution package directly.
"""

from __future__ import annotations

from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.distribution import DistributionService
from ai_multi_agent_platform.distribution.control_plane import RegistryCommandHandlers
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.technical_catalog import derive_technical_metadata
from ai_multi_agent_platform.onboarding.setup_registry_contracts import (
    SetupRegistryDependency,
    SetupRegistryItem,
    SetupRegistryTechnicalMetadata,
)


class DistributionSetupRegistryPort:
    """Project canonical Registry reads and mutations into the setup-owned port."""

    def __init__(
        self,
        distribution: DistributionService,
        commands: RegistryCommandHandlers | None,
    ) -> None:
        self._distribution = distribution
        self._commands = commands

    @property
    def enabled(self) -> bool:
        return self._distribution.enabled

    @property
    def mutation_enabled(self) -> bool:
        return self._commands is not None and self._distribution.activation_enabled

    def search(self) -> tuple[SetupRegistryItem, ...]:
        return tuple(self._project(item) for item in self._distribution.search())

    def get(self, item_id: str, version: str) -> SetupRegistryItem:
        return self._project(self._distribution.get(item_id, version))

    def installed_version(self, item_id: str) -> str | None:
        installation = self._distribution.installed(item_id)
        return None if installation is None else installation.current.version

    async def preview(
        self,
        context: RequestContext,
        item_id: str,
        version: str,
    ) -> None:
        commands = self._require_commands()
        await commands.preview(context, item_id, {"version": version})

    async def activate(
        self,
        context: RequestContext,
        item_id: str,
        version: str,
    ) -> None:
        commands = self._require_commands()
        await commands.activate(context, item_id, {"version": version})

    def _require_commands(self) -> RegistryCommandHandlers:
        if self._commands is None:
            raise RuntimeError("Registry activation authority is not configured")
        return self._commands

    @staticmethod
    def _project(item: RegistryItem) -> SetupRegistryItem:
        technical = derive_technical_metadata(item)
        return SetupRegistryItem(
            item_id=item.item_id,
            version=item.version,
            name=item.name,
            description=item.description,
            categories=tuple(sorted(item.categories)),
            route=item.route.value,
            deprecated=item.deprecated,
            yanked=item.yanked,
            dependencies=tuple(
                SetupRegistryDependency(
                    item_id=dependency.item_id,
                    minimum_version=dependency.version_range.minimum,
                    maximum_version=dependency.version_range.maximum,
                    optional=dependency.optional,
                )
                for dependency in item.dependencies
            ),
            license=item.license,
            source_repository=item.source.repository,
            technical=(
                None
                if technical is None
                else SetupRegistryTechnicalMetadata(
                    deployment_modes=technical.deployment_modes,
                    network_status=technical.network_status,
                    lifecycle_status=technical.lifecycle_status,
                )
            ),
        )


__all__ = ["DistributionSetupRegistryPort"]
