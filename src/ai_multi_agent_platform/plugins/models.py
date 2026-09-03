"""Backend-neutral plugin manifest and lifecycle models for issue #20."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue

PLUGIN_MANIFEST_VERSION = "1"
_VERSION_PATTERN = re.compile(r"^\d+(?:\.\d+){0,2}$")
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class ExtensionType(StrEnum):
    ORCHESTRATOR = "orchestrator"
    EXECUTOR = "executor"
    MODEL_PROVIDER = "model_provider"
    MODEL_ROUTING_POLICY = "model_routing_policy"
    CAPABILITY_PROVIDER = "capability_provider"
    MEMORY_PROVIDER = "memory_provider"
    FILE_PROVIDER = "file_provider"
    KNOWLEDGE_PROVIDER = "knowledge_provider"
    EVENT_PROVIDER = "event_provider"
    AUTHORIZATION_PROVIDER = "authorization_provider"
    OBSERVABILITY_EXPORTER = "observability_exporter"
    AUTOMATION_PROVIDER = "automation_provider"
    EVALUATOR = "evaluator"
    NODE_PROVIDER = "node_provider"
    WORKER_PROVIDER = "worker_provider"
    CONNECTOR_PROVIDER = "connector_provider"
    FRONTEND_EXTENSION = "frontend_extension"


class PluginPermission(StrEnum):
    NETWORK_ACCESS = "network_access"
    WORKSPACE_ACCESS = "workspace_access"
    CAPABILITY_REGISTRATION = "capability_registration"
    SECRET_CONSUMPTION = "secret_consumption"
    WORKER_EXECUTION = "worker_execution"
    ADMINISTRATIVE_API = "administrative_api"
    FRONTEND_EXTENSION = "frontend_extension"


class PluginState(StrEnum):
    INSTALLED = "installed"
    CONFIGURED = "configured"
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"


class PluginHealth(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class CompatibilityState(StrEnum):
    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class VersionRange:
    minimum: str | None = None
    maximum: str | None = None

    def __post_init__(self) -> None:
        if self.minimum is not None:
            _require_numeric_version(self.minimum, "minimum version")
        if self.maximum is not None:
            _require_numeric_version(self.maximum, "maximum version")
        if self.minimum is not None and self.maximum is not None:
            if _version_key(self.minimum) > _version_key(self.maximum):
                raise ValueError("minimum version must not be greater than maximum version")

    def contains(self, version: str) -> bool:
        candidate = _version_key(version)
        if self.minimum is not None and candidate < _version_key(self.minimum):
            return False
        if self.maximum is not None and candidate > _version_key(self.maximum):
            return False
        return True


@dataclass(frozen=True, slots=True)
class PluginExtensionSpec:
    extension_id: str
    extension_type: ExtensionType
    interface_version: str
    entrypoint: str
    metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.extension_id, "extension_id")
        _require_numeric_version(self.interface_version, "interface_version")
        _require_non_blank(self.entrypoint, "entrypoint")


@dataclass(frozen=True, slots=True)
class PluginDependency:
    plugin_id: str
    version_range: VersionRange = field(default_factory=VersionRange)
    optional: bool = False

    def __post_init__(self) -> None:
        _require_id(self.plugin_id, "dependency plugin_id")


@dataclass(frozen=True, slots=True)
class PluginProvenance:
    source: str
    license: str
    source_repository: str | None = None
    revision: str | None = None
    checksum: str | None = None
    trust_source: str | None = None
    local_modifications: str | None = None

    def __post_init__(self) -> None:
        _require_non_blank(self.source, "provenance source")
        _require_non_blank(self.license, "license")


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    name: str
    description: str
    plugin_version: str
    author: str
    provenance: PluginProvenance
    extensions: tuple[PluginExtensionSpec, ...]
    supported_platform: VersionRange = field(default_factory=VersionRange)
    manifest_version: str = PLUGIN_MANIFEST_VERSION
    requested_permissions: frozenset[PluginPermission] = frozenset()
    configuration_schema: dict[str, JsonValue] = field(
        default_factory=lambda: {"type": "object", "additionalProperties": False}
    )
    dependencies: tuple[PluginDependency, ...] = ()
    optional_external_services: tuple[str, ...] = ()
    state_migrations: tuple[str, ...] = ()
    ui_metadata: dict[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_id(self.plugin_id, "plugin_id")
        _require_non_blank(self.name, "name")
        _require_non_blank(self.description, "description")
        _require_numeric_version(self.plugin_version, "plugin_version")
        _require_non_blank(self.author, "author")
        _require_non_blank(self.manifest_version, "manifest_version")
        extension_ids = [extension.extension_id for extension in self.extensions]
        if len(set(extension_ids)) != len(extension_ids):
            raise ValueError("plugin manifest contains duplicate extension IDs")
        dependency_ids = [dependency.plugin_id for dependency in self.dependencies]
        if self.plugin_id in dependency_ids:
            raise ValueError("plugin cannot depend on itself")
        if len(set(dependency_ids)) != len(dependency_ids):
            raise ValueError("plugin manifest contains duplicate dependencies")


@dataclass(frozen=True, slots=True)
class PluginHealthReport:
    health: PluginHealth
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PluginSnapshot:
    plugin_id: str
    plugin_version: str
    state: PluginState
    compatibility: CompatibilityState
    health: PluginHealth
    extension_ids: tuple[str, ...]
    requested_permissions: tuple[str, ...]
    granted_permissions: tuple[str, ...]
    configured: bool
    health_detail: str | None = None


def _require_non_blank(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must be non-blank")


def _require_id(value: str, field_name: str) -> None:
    if not _ID_PATTERN.fullmatch(value):
        raise ValueError(
            f"{field_name} must use lowercase alphanumeric segments separated by '.', '_' or '-'"
        )


def _require_numeric_version(value: str, field_name: str) -> None:
    if not _VERSION_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} must be a one-to-three-part numeric dotted version")


def _version_key(value: str) -> tuple[int, int, int]:
    _require_numeric_version(value, "version")
    parts = [int(part) for part in value.split(".")]
    parts.extend([0] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]
