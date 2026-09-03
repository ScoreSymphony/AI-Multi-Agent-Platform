"""Versioned plugin SDK and lifecycle foundation."""

from .binders import CapabilityRegistryBinder
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
    VersionRange,
)
from .reference import ReferenceCapabilityPlugin, reference_manifest
from .registry import PluginRegistry
from .runtime import ExtensionBinder, ExtensionRegistration, PluginContext, PluginRuntime

__all__ = [
    "PLUGIN_MANIFEST_SCHEMA",
    "CapabilityRegistryBinder",
    "CompatibilityState",
    "ExtensionBinder",
    "ExtensionRegistration",
    "ExtensionType",
    "PluginContext",
    "PluginDependency",
    "PluginExtensionSpec",
    "PluginHealth",
    "PluginHealthReport",
    "PluginManifest",
    "PluginPermission",
    "PluginProvenance",
    "PluginRegistry",
    "PluginRuntime",
    "PluginSnapshot",
    "PluginState",
    "ReferenceCapabilityPlugin",
    "VersionRange",
    "reference_manifest",
    "validate_manifest_document",
]
