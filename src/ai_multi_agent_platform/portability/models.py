"""Canonical portable package models for issue #79.

The package contracts are platform-owned and intentionally describe canonical resources,
requirements and exclusions without carrying backend-private runtime state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue

PORTABLE_FORMAT_VERSION = "1.0"
PORTABLE_INTEGRITY_ALGORITHM = "sha256"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class DependencyKind(StrEnum):
    RESOURCE = "resource"
    PLUGIN = "plugin"
    CAPABILITY = "capability"
    CONNECTOR = "connector"
    MODEL = "model"
    SECRET = "secret"


class IdPolicy(StrEnum):
    """Canonical identity behavior requested for one imported resource."""

    PRESERVE = "preserve"
    REGENERATE = "regenerate"
    HISTORICAL_PRESERVE = "historical_preserve"


class ExclusionCategory(StrEnum):
    PLAINTEXT_SECRET = "plaintext_secret"
    BACKEND_RUNTIME_STATE = "backend_runtime_state"
    REBUILDABLE_INDEX = "rebuildable_index"
    PROVIDER_PRIVATE_STATE = "provider_private_state"
    POLICY = "policy"


@dataclass(frozen=True, slots=True)
class DependencyRequirement:
    kind: DependencyKind
    identifier: str
    required: bool = True
    version_constraint: str | None = None
    purpose: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.identifier, "dependency identifier")
        if self.version_constraint is not None:
            _require_nonblank(self.version_constraint, "dependency version constraint")
        if self.purpose is not None:
            _require_nonblank(self.purpose, "dependency purpose")


@dataclass(frozen=True, slots=True)
class ExcludedState:
    category: ExclusionCategory
    path: str
    reason: str
    resource_type: str | None = None
    resource_id: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.path, "excluded-state path")
        _require_nonblank(self.reason, "excluded-state reason")
        if (self.resource_type is None) != (self.resource_id is None):
            raise ValueError(
                "excluded-state resource_type and resource_id must both be set or omitted"
            )
        if self.resource_type is not None:
            _require_nonblank(self.resource_type, "excluded-state resource_type")
        if self.resource_id is not None:
            _require_nonblank(self.resource_id, "excluded-state resource_id")


@dataclass(frozen=True, slots=True)
class PackageProvenance:
    source: str
    author: str | None = None
    source_instance_id: str | None = None
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.source, "provenance source")
        if self.author is not None:
            _require_nonblank(self.author, "provenance author")
        if self.source_instance_id is not None:
            _require_nonblank(self.source_instance_id, "provenance source_instance_id")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class CompatibilityMetadata:
    minimum_platform_version: str | None = None
    maximum_platform_version: str | None = None
    contract_versions: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.minimum_platform_version is not None:
            _require_nonblank(self.minimum_platform_version, "minimum platform version")
        if self.maximum_platform_version is not None:
            _require_nonblank(self.maximum_platform_version, "maximum platform version")
        copied = dict(self.contract_versions)
        for name, version in copied.items():
            _require_nonblank(name, "compatibility contract name")
            _require_nonblank(version, "compatibility contract version")
        object.__setattr__(self, "contract_versions", copied)


@dataclass(frozen=True, slots=True)
class PortableResource:
    """One canonical serialized resource carried by a portable package."""

    resource_type: str
    resource_id: str
    resource_version: str
    payload: dict[str, JsonValue]
    id_policy: IdPolicy = IdPolicy.PRESERVE
    dependencies: tuple[DependencyRequirement, ...] = ()
    checksum: str = ""

    def __post_init__(self) -> None:
        _require_nonblank(self.resource_type, "portable resource type")
        _require_nonblank(self.resource_id, "portable resource ID")
        _require_nonblank(self.resource_version, "portable resource version")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("portable resource dependencies must be unique")
        object.__setattr__(self, "payload", dict(self.payload))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.resource_type, self.resource_id, self.resource_version)


@dataclass(frozen=True, slots=True)
class PortableResourceDescriptor:
    resource_type: str
    resource_id: str
    resource_version: str
    id_policy: IdPolicy
    checksum: str
    dependencies: tuple[DependencyRequirement, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank(self.resource_type, "resource descriptor type")
        _require_nonblank(self.resource_id, "resource descriptor ID")
        _require_nonblank(self.resource_version, "resource descriptor version")
        _require_nonblank(self.checksum, "resource descriptor checksum")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("resource descriptor dependencies must be unique")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.resource_type, self.resource_id, self.resource_version)

    @classmethod
    def from_resource(cls, resource: PortableResource) -> PortableResourceDescriptor:
        if not resource.checksum:
            raise ValueError("portable resource must be sealed before creating a descriptor")
        return cls(
            resource_type=resource.resource_type,
            resource_id=resource.resource_id,
            resource_version=resource.resource_version,
            id_policy=resource.id_policy,
            checksum=resource.checksum,
            dependencies=resource.dependencies,
        )


@dataclass(frozen=True, slots=True)
class PortablePackageManifest:
    """Versioned public manifest for one portable import/export package."""

    source_platform_version: str
    resources: tuple[PortableResourceDescriptor, ...]
    provenance: PackageProvenance
    compatibility: CompatibilityMetadata = field(default_factory=CompatibilityMetadata)
    requirements: tuple[DependencyRequirement, ...] = ()
    excluded_state: tuple[ExcludedState, ...] = ()
    created_at: datetime = field(default_factory=_utc_now)
    format_version: str = PORTABLE_FORMAT_VERSION
    integrity_algorithm: str = PORTABLE_INTEGRITY_ALGORITHM

    def __post_init__(self) -> None:
        _require_nonblank(self.source_platform_version, "source platform version")
        _require_nonblank(self.format_version, "portable format version")
        _require_nonblank(self.integrity_algorithm, "portable integrity algorithm")
        _require_aware(self.created_at, "portable package created_at")
        identities = [resource.identity for resource in self.resources]
        if len(set(identities)) != len(identities):
            raise ValueError("portable package resource descriptors must have unique identities")
        if len(set(self.requirements)) != len(self.requirements):
            raise ValueError("portable package requirements must be unique")


@dataclass(frozen=True, slots=True)
class PortablePackage:
    manifest: PortablePackageManifest
    resources: tuple[PortableResource, ...]
    checksum: str

    def __post_init__(self) -> None:
        identities = [resource.identity for resource in self.resources]
        if len(set(identities)) != len(identities):
            raise ValueError("portable package resources must have unique identities")
        _require_nonblank(self.checksum, "portable package checksum")
