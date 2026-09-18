"""Canonical plugin-owner adapter for verified Registry plugin manifests."""

from __future__ import annotations

import json
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
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

from ai_multi_agent_platform.security.redaction import redact_sensitive

from .items import RegistryItem
from .models import RegistryItemType


class PluginRegistryArtifactInstaller:
    """Install or explicitly update a verified manifest through the canonical plugin owner."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def validated_manifest(self, item: RegistryItem, artifact: bytes) -> PluginManifest:
        """Validate a Registry artifact against the canonical plugin manifest contract."""

        try:
            document = json.loads(artifact.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "registry plugin artifact must be a UTF-8 JSON canonical plugin manifest",
            ) from exc
        validate_manifest_document(document)
        if not isinstance(document, dict):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION, "plugin manifest must be an object"
            )
        if item.item_type is RegistryItemType.MODEL_PROVIDER:
            _assert_no_plaintext_credentials(
                document,
                label="Model Provider",
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
        return manifest

    async def install_verified_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        manifest = self.validated_manifest(item, artifact)
        install_source = f"registry:{item.source.repository}@{item.version}"
        try:
            current = self._registry.get(manifest.plugin_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        else:
            if current.plugin_version == manifest.plugin_version:
                if self._registry.manifest(manifest.plugin_id) != manifest:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "installed plugin manifest differs from Registry artifact at same version",
                    )
                return current
            return self._registry.apply_update(
                manifest.plugin_id,
                manifest,
                install_source=install_source,
            )
        return self._registry.install(manifest, install_source=install_source)


def _sensitive_mapping_key(key: str) -> bool:
    probe = cast(JsonValue, {key: "marketplace-sensitive-probe"})
    return redact_sensitive(probe) != probe


def _contains_nonempty_schema_value(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip()) and value != "[REDACTED]"
    if isinstance(value, list):
        return any(_contains_nonempty_schema_value(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_nonempty_schema_value(item) for item in value.values())
    return False


def _assert_value_free_sensitive_schema(
    schema: object,
    *,
    label: str,
    sensitive_context: bool = False,
) -> None:
    if isinstance(schema, list):
        for item in schema:
            _assert_value_free_sensitive_schema(
                item,
                label=label,
                sensitive_context=sensitive_context,
            )
        return
    if not isinstance(schema, dict):
        return

    if sensitive_context:
        for field in ("default", "const", "example", "examples", "enum"):
            if field in schema and _contains_nonempty_schema_value(schema[field]):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        f"{label} Marketplace configuration schema must not embed "
                        f"credential values in {field}"
                    ),
                )

    properties = schema.get("properties")
    if isinstance(properties, dict):
        for property_name, property_schema in properties.items():
            child_sensitive = sensitive_context or (
                isinstance(property_name, str) and _sensitive_mapping_key(property_name)
            )
            _assert_value_free_sensitive_schema(
                property_schema,
                label=label,
                sensitive_context=child_sensitive,
            )

    for keyword in (
        "$defs",
        "definitions",
        "items",
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "then",
        "else",
        "additionalProperties",
    ):
        if keyword in schema:
            _assert_value_free_sensitive_schema(
                schema[keyword],
                label=label,
                sensitive_context=sensitive_context,
            )


def _assert_no_plaintext_credentials(
    document: dict[str, Any],
    *,
    label: str,
) -> None:
    """Keep distributable provider packages separate from configured secret values."""

    scan_document = dict(document)
    configuration_schema = scan_document.pop("configuration_schema", None)
    json_document = cast(JsonValue, scan_document)
    if redact_sensitive(json_document) != json_document:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            (
                f"{label} Marketplace package must not embed plaintext credentials; "
                "configure canonical secret references after installation"
            ),
        )
    _assert_value_free_sensitive_schema(configuration_schema, label=label)


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
