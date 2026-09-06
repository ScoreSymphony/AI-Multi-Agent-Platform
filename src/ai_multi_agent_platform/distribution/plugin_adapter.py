"""Canonical #20 owner adapter for verified Registry plugin manifests."""

from __future__ import annotations

import json
from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import PluginManifest, PluginRegistry
from ai_multi_agent_platform.plugins.manifest import validate_manifest_document
from ai_multi_agent_platform.plugins.models import (
    ExtensionType,
    PluginDependency,
    PluginExtensionSpec,
    PluginPermission,
    PluginProvenance,
    PluginStateMigrationSpec,
    VersionRange,
)

from .items import RegistryItem


class PluginRegistryArtifactInstaller:
    """Install or explicitly update a verified manifest through the canonical #20 owner."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    async def install_verified_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        try:
            document = json.loads(artifact.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "registry plugin artifact must be a UTF-8 JSON #20 manifest",
            ) from exc
        validate_manifest_document(document)
        if not isinstance(document, dict):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION, "plugin manifest must be an object"
            )
        manifest = _manifest_from_document(document)
        if manifest.plugin_id != item.item_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry item ID does not match plugin manifest plugin_id",
            )
        if manifest.plugin_version != item.version:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry item version does not match plugin manifest plugin_version",
            )
        if manifest.provenance.license != item.license:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry license does not match plugin manifest provenance license",
            )
        install_source = f"registry:{item.source.repository}@{item.version}"
        try:
            current = self._registry.get(manifest.plugin_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        else:
            if current.plugin_version == manifest.plugin_version:
                return current
            return self._registry.apply_update(
                manifest.plugin_id,
                manifest,
                install_source=install_source,
            )
        return self._registry.install(manifest, install_source=install_source)


def _manifest_from_document(document: dict[str, Any]) -> PluginManifest:
    provenance = document["provenance"]
    supported_platform = document["supported_platform"]
    return PluginManifest(
        plugin_id=document["plugin_id"],
        name=document["name"],
        description=document["description"],
        plugin_version=document["plugin_version"],
        manifest_version=document["manifest_version"],
        author=document["author"],
        provenance=PluginProvenance(
            source=provenance["source"],
            license=provenance["license"],
            source_repository=provenance.get("source_repository"),
            revision=provenance.get("revision"),
            checksum=provenance.get("checksum"),
            trust_source=provenance.get("trust_source"),
            local_modifications=provenance.get("local_modifications"),
        ),
        supported_platform=VersionRange(
            supported_platform.get("minimum"), supported_platform.get("maximum")
        ),
        extensions=tuple(
            PluginExtensionSpec(
                extension_id=extension["extension_id"],
                extension_type=ExtensionType(extension["extension_type"]),
                interface_version=extension["interface_version"],
                entrypoint=extension["entrypoint"],
                metadata=dict(extension.get("metadata", {})),
            )
            for extension in document["extensions"]
        ),
        capabilities=tuple(document["capabilities"]),
        requested_permissions=frozenset(
            PluginPermission(permission) for permission in document["requested_permissions"]
        ),
        configuration_version=document["configuration_version"],
        configuration_schema=dict(document["configuration_schema"]),
        dependencies=tuple(
            PluginDependency(
                plugin_id=dependency["plugin_id"],
                version_range=VersionRange(
                    dependency["version_range"].get("minimum"),
                    dependency["version_range"].get("maximum"),
                ),
                optional=dependency["optional"],
            )
            for dependency in document["dependencies"]
        ),
        optional_external_services=tuple(document.get("optional_external_services", [])),
        state_version=document["state_version"],
        state_migrations=tuple(
            PluginStateMigrationSpec(
                migration_id=migration["migration_id"],
                from_version=migration["from_version"],
                to_version=migration["to_version"],
            )
            for migration in document["state_migrations"]
        ),
        ui_metadata=dict(document.get("ui_metadata", {})),
    )
