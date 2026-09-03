"""Versioned plugin SDK and lifecycle foundation."""

from .binders import CapabilityRegistryBinder
from .discovery import (
    DiscoveredPlugin,
    PluginCatalog,
    PluginRuntimeFactory,
    PluginSource,
    StaticPluginSource,
)
from .manifest import PLUGIN_MANIFEST_SCHEMA, validate_manifest_document
from .models import (
    CompatibilityState,
    ExtensionType,
    PluginDependency,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginManifest,
    PluginPermission,
    PluginProvenance,
    PluginSnapshot,
    PluginState,
    PluginStateMigrationSpec,
    VersionRange,
)
from .reference import ReferenceCapabilityPlugin, reference_manifest
from .registry import PluginRegistry
from .runtime import ExtensionBinder, ExtensionRegistration, PluginContext, PluginRuntime
from .state import (
    InMemoryPluginStateStore,
    PluginOwnedState,
    PluginStateMigration,
    PluginStateMigrator,
    PluginStateStore,
)

__all__ = [
    "PLUGIN_MANIFEST_SCHEMA",
    "CapabilityRegistryBinder",
    "CompatibilityState",
    "DiscoveredPlugin",
    "ExtensionBinder",
    "ExtensionRegistration",
    "ExtensionType",
    "InMemoryPluginStateStore",
    "PluginCatalog",
    "PluginContext",
    "PluginDependency",
    "PluginExtensionSpec",
    "PluginHealth",
    "PluginHealthReport",
    "PluginManifest",
    "PluginOwnedState",
    "PluginPermission",
    "PluginProvenance",
    "PluginRegistry",
    "PluginRuntime",
    "PluginRuntimeFactory",
    "PluginSnapshot",
    "PluginSource",
    "PluginState",
    "PluginStateMigration",
    "PluginStateMigrationSpec",
    "PluginStateMigrator",
    "PluginStateStore",
    "ReferenceCapabilityPlugin",
    "StaticPluginSource",
    "VersionRange",
    "reference_manifest",
    "validate_manifest_document",
]
