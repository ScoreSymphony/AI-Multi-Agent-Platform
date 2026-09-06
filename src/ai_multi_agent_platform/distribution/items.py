"""Registry item, installed-item and discovery query contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable
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

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


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

    def __post_init__(self) -> None:
        _require_id(self.item_id, "item_id")
        version_key(self.version)
        for value, field_name in (
            (self.name, "name"),
            (self.description, "description"),
            (self.publisher, "publisher"),
            (self.license, "license"),
            (self.provenance, "provenance"),
        ):
            _require_text(value, field_name)
        for values, field_name in (
            (self.requested_permissions, "requested_permissions"),
            (self.required_capabilities, "required_capabilities"),
            (self.required_plugins, "required_plugins"),
            (self.required_connectors, "required_connectors"),
            (self.required_models, "required_models"),
            (self.tags, "tags"),
            (self.categories, "categories"),
        ):
            _require_nonblank_values(values, field_name)
        for optional_value, optional_field_name in (
            (self.review_reference, "review_reference"),
            (self.released_at, "released_at"),
            (self.changelog, "changelog"),
        ):
            if optional_value is not None:
                _require_text(optional_value, optional_field_name)

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

    def __post_init__(self) -> None:
        if self.text is not None:
            _require_text(self.text, "query text")
        if self.platform_version is not None:
            version_key(self.platform_version)
        if self.update_for_item_id is not None:
            _require_id(self.update_for_item_id, "update_for_item_id")
        for values, field_name in (
            (self.tags, "query tags"),
            (self.categories, "query categories"),
            (self.licenses, "query licenses"),
            (self.publishers, "query publishers"),
            (self.required_capabilities, "query required_capabilities"),
        ):
            _require_nonblank_values(values, field_name)


@dataclass(frozen=True, slots=True)
class InstalledRegistryItem:
    item_id: str
    version: str
    source_registry: str
    pinned_version: str | None = None
    license: str | None = None
    provenance: str | None = None

    def __post_init__(self) -> None:
        _require_id(self.item_id, "installed item_id")
        version_key(self.version)
        _require_text(self.source_registry, "source_registry")
        if self.pinned_version is not None:
            version_key(self.pinned_version)
        if self.license is not None:
            _require_text(self.license, "installed license")
        if self.provenance is not None:
            _require_text(self.provenance, "installed provenance")

    def has_update(self, candidate: RegistryItem) -> bool:
        """Return whether the candidate is a newer release, independent from update policy."""

        return candidate.item_id == self.item_id and version_key(candidate.version) > version_key(
            self.version
        )

    def accepts_update(self, candidate: RegistryItem) -> bool:
        """Return whether the candidate may be applied under the current version pin."""

        if not self.has_update(candidate):
            return False
        return self.pinned_version is None or candidate.version == self.pinned_version


def _require_id(value: str, field_name: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"{field_name} has invalid canonical ID syntax")


def _require_text(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-blank")


def _require_nonblank_values(values: Iterable[object], field_name: str) -> None:
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must contain only non-blank strings")
