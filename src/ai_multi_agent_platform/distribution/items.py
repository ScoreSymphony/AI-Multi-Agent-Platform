"""Registry item, installed-item and discovery query contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import (
    ArtifactIntegrity,
    DistributionRoute,
    RegistryDependency,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    VersionRange,
    version_key,
)


@dataclass(frozen=True, slots=True)
class RegistryItem:
    item_id: str
    item_type: RegistryItemType
    name: str
    description: str
    version: str
    publisher: str
    source: RegistrySource
    license: str
    provenance: str
    supported_platform: VersionRange = field(default_factory=VersionRange)
    dependencies: tuple[RegistryDependency, ...] = ()
    requested_permissions: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    required_plugins: tuple[str, ...] = ()
    required_connectors: tuple[str, ...] = ()
    required_models: tuple[str, ...] = ()
    tags: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    integrity: ArtifactIntegrity = field(default_factory=ArtifactIntegrity)
    trust_status: TrustStatus = TrustStatus.UNTRUSTED
    review_reference: str | None = None
    released_at: str | None = None
    changelog: str | None = None
    deprecated: bool = False
    yanked: bool = False

    @property
    def route(self) -> DistributionRoute:
        if self.item_type is RegistryItemType.PLUGIN:
            return DistributionRoute.PLUGIN
        if self.item_type is RegistryItemType.DOCUMENTATION:
            return DistributionRoute.MANUAL
        return DistributionRoute.PORTABLE_IMPORT


@dataclass(frozen=True, slots=True)
class RegistryQuery:
    text: str | None = None
    item_types: frozenset[RegistryItemType] = frozenset()
    tags: frozenset[str] = frozenset()
    categories: frozenset[str] = frozenset()
    licenses: frozenset[str] = frozenset()
    publishers: frozenset[str] = frozenset()
    required_capabilities: frozenset[str] = frozenset()
    trust_statuses: frozenset[TrustStatus] = frozenset()
    platform_version: str | None = None
    include_deprecated: bool = False
    include_yanked: bool = False
    update_for_item_id: str | None = None


@dataclass(frozen=True, slots=True)
class InstalledRegistryItem:
    item_id: str
    version: str
    source_registry: str
    pinned_version: str | None = None
    license: str | None = None
    provenance: str | None = None

    def accepts_update(self, candidate: RegistryItem) -> bool:
        if candidate.item_id != self.item_id:
            return False
        if self.pinned_version is not None and candidate.version != self.pinned_version:
            return False
        return version_key(candidate.version) > version_key(self.version)
