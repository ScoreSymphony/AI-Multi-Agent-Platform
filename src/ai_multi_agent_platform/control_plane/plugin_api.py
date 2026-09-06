"""Canonical northbound plugin lifecycle composition for issue #20.

The plugin runtime remains optional. When a PluginRegistry is supplied, this module
registers platform-owned plugin resources and lifecycle commands through the existing
versioned Control Plane extension seams. CLI/UI clients therefore never talk directly
to PluginRegistry or PluginCatalog.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.plugins import (
    DiscoveredPlugin,
    PluginCatalog,
    PluginManifest,
    PluginPermission,
    PluginRegistry,
    PluginSnapshot,
)

from .authenticated_authorization import ControlPlane as _CurrentControlPlane
from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext

PLUGIN_COLLECTION = "plugins"
PLUGIN_CANDIDATE_COLLECTION = "plugin-candidates"
PLUGIN_COLLECTIONS = (PLUGIN_COLLECTION, PLUGIN_CANDIDATE_COLLECTION)
PLUGIN_COMMANDS = (
    "plugin.install",
    "plugin.configure",
    "plugin.enable",
    "plugin.disable",
    "plugin.refresh-health",
    "plugin.validate-update",
    "plugin.remove",
)

PluginPermissionResolver = Callable[
    [RequestContext, PluginManifest], Awaitable[frozenset[PluginPermission]]
]


class _PluginResources(ResourceService):
    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _plugin_resource(self._registry, item) for item in self._registry.list_plugins()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _plugin_resource(self._registry, self._registry.get(resource_id))


class _PluginCandidateResources(ResourceService):
    def __init__(self, catalog: PluginCatalog) -> None:
        self._catalog = catalog

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_candidate_resource(item) for item in self._catalog.refresh())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        self._catalog.refresh()
        return _candidate_resource(self._catalog.candidate(resource_id))


class ControlPlane(_CurrentControlPlane):
    """Current composed Control Plane plus optional canonical plugin lifecycle."""

    def __init__(
        self,
        *args: Any,
        plugin_registry: PluginRegistry | None = None,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
        **kwargs: Any,
    ) -> None:
        if plugin_catalog is not None and plugin_registry is None:
            raise ValueError("plugin_catalog requires plugin_registry")
        super().__init__(*args, **kwargs)
        self._plugin_registry = plugin_registry
        self._plugin_catalog = plugin_catalog
        self._plugin_permission_resolver = plugin_permission_resolver

        if plugin_registry is None:
            return

        super().register_resource_service(PLUGIN_COLLECTION, _PluginResources(plugin_registry))
        if plugin_catalog is not None:
            super().register_resource_service(
                PLUGIN_CANDIDATE_COLLECTION,
                _PluginCandidateResources(plugin_catalog),
            )
        super().register_command("plugin.install", self._plugin_install_command)
        super().register_command("plugin.configure", self._plugin_configure_command)
        super().register_command("plugin.enable", self._plugin_enable_command)
        super().register_command("plugin.disable", self._plugin_disable_command)
        super().register_command("plugin.refresh-health", self._plugin_refresh_health_command)
        super().register_command("plugin.validate-update", self._plugin_validate_update_command)
        super().register_command("plugin.remove", self._plugin_remove_command)

    @property
    def plugin_registry(self) -> PluginRegistry | None:
        return self._plugin_registry

    @property
    def plugin_catalog(self) -> PluginCatalog | None:
        return self._plugin_catalog

    def attach_plugin_runtime(
        self,
        plugin_registry: PluginRegistry,
        *,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
    ) -> None:
        """Attach the optional #20 lifecycle once after outer composition is available.

        Some production compositions can only construct plugin binders after their canonical
        registries (for example the Capability Registry) exist. This seam preserves one #20
        PluginRegistry while still allowing that outer composition to happen after Control Plane
        construction. It may be called only when no plugin runtime was configured initially.
        """

        if (
            self._plugin_registry is not None
            or self._plugin_catalog is not None
            or self._plugin_permission_resolver is not None
        ):
            raise ValueError("plugin runtime is already configured")
        self._plugin_registry = plugin_registry
        self._plugin_catalog = plugin_catalog
        self._plugin_permission_resolver = plugin_permission_resolver

        super().register_resource_service(PLUGIN_COLLECTION, _PluginResources(plugin_registry))
        if plugin_catalog is not None:
            super().register_resource_service(
                PLUGIN_CANDIDATE_COLLECTION,
                _PluginCandidateResources(plugin_catalog),
            )
        super().register_command("plugin.install", self._plugin_install_command)
        super().register_command("plugin.configure", self._plugin_configure_command)
        super().register_command("plugin.enable", self._plugin_enable_command)
        super().register_command("plugin.disable", self._plugin_disable_command)
        super().register_command("plugin.refresh-health", self._plugin_refresh_health_command)
        super().register_command("plugin.validate-update", self._plugin_validate_update_command)
        super().register_command("plugin.remove", self._plugin_remove_command)

    def register_resource_service(self, collection: str, service: ResourceService) -> None:
        if collection in PLUGIN_COLLECTIONS:
            raise ValueError(
                f"extension collection conflicts with canonical plugin route: {collection}"
            )
        super().register_resource_service(collection, service)

    def register_command(self, command: str, handler: CommandHandler) -> None:
        if command in PLUGIN_COMMANDS:
            raise ValueError(
                f"extension command conflicts with canonical plugin command: {command}"
            )
        super().register_command(command, handler)

    async def _plugin_install_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        registry = self._require_plugin_registry()
        catalog = self._require_plugin_catalog()
        _require_only(payload, {"manifest_digest"})
        expected_digest = _required_string(payload, "manifest_digest")
        catalog.refresh()
        candidate = catalog.candidate(resource_ref)
        _require_manifest_digest(candidate.manifest, expected_digest)
        snapshot = catalog.install(resource_ref, registry)
        return _plugin_resource(registry, snapshot)

    async def _plugin_configure_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, {"configuration"})
        configuration = payload.get("configuration")
        if not isinstance(configuration, dict):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "plugin configuration must be a JSON object",
                details={"field": "configuration"},
            )
        registry = self._require_plugin_registry()
        snapshot = registry.configure(resource_ref, configuration)
        return _plugin_resource(registry, snapshot)

    async def _plugin_enable_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_only(payload, {"manifest_digest"})
        expected_digest = _required_string(payload, "manifest_digest")
        registry = self._require_plugin_registry()
        installed_manifest = registry.manifest(resource_ref)
        _require_manifest_digest(installed_manifest, expected_digest)

        catalog = self._require_plugin_catalog()
        catalog.refresh()
        candidate = catalog.candidate(resource_ref)
        if candidate.manifest != installed_manifest:
            raise ContractError(
                ErrorCode.CONFLICT,
                (
                    f"discovered plugin {resource_ref!r} no longer matches the installed manifest; "
                    "validate the update before activation"
                ),
            )

        granted = await self._granted_permissions(context, installed_manifest)
        runtime = catalog.create_runtime(resource_ref)
        snapshot = await registry.enable(
            resource_ref,
            runtime,
            granted_permissions=granted,
        )
        return _plugin_resource(registry, snapshot)

    async def _plugin_disable_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        registry = self._require_plugin_registry()
        snapshot = await registry.disable(resource_ref)
        return _plugin_resource(registry, snapshot)

    async def _plugin_refresh_health_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        registry = self._require_plugin_registry()
        snapshot = await registry.refresh_health(resource_ref)
        return _plugin_resource(registry, snapshot)

    async def _plugin_validate_update_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, {"manifest_digest"})
        expected_digest = _required_string(payload, "manifest_digest")
        registry = self._require_plugin_registry()
        current = registry.get(resource_ref)
        catalog = self._require_plugin_catalog()
        catalog.refresh()
        candidate = catalog.candidate(resource_ref)
        _require_manifest_digest(candidate.manifest, expected_digest)
        registry.validate_update(resource_ref, candidate.manifest)
        return {
            "id": resource_ref,
            "type": "plugin-update-validation",
            "compatible": True,
            "current_version": current.plugin_version,
            "candidate_version": candidate.manifest.plugin_version,
            "manifest_digest": expected_digest,
        }

    async def _plugin_remove_command(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        registry = self._require_plugin_registry()
        before = registry.get(resource_ref)
        registry.remove(resource_ref)
        return {
            "id": resource_ref,
            "type": "plugin-removal",
            "removed": True,
            "plugin_version": before.plugin_version,
        }

    def _require_plugin_registry(self) -> PluginRegistry:
        if self._plugin_registry is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "plugin registry is not configured")
        return self._plugin_registry

    def _require_plugin_catalog(self) -> PluginCatalog:
        if self._plugin_catalog is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "plugin discovery catalog is not configured")
        return self._plugin_catalog

    async def _granted_permissions(
        self,
        context: RequestContext,
        manifest: PluginManifest,
    ) -> frozenset[PluginPermission]:
        if self._plugin_permission_resolver is None:
            if manifest.requested_permissions:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    (
                        f"plugin {manifest.plugin_id!r} requests permissions but no authoritative "
                        "plugin permission resolver is configured"
                    ),
                )
            return frozenset()

        granted = await self._plugin_permission_resolver(context, deepcopy(manifest))
        if not isinstance(granted, frozenset) or any(
            not isinstance(permission, PluginPermission) for permission in granted
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "plugin permission resolver returned invalid permission values",
            )
        unexpected = granted - manifest.requested_permissions
        if unexpected:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "plugin permission resolver attempted to grant undeclared permissions",
                details={
                    "unexpected_permissions": cast(
                        JsonValue,
                        sorted(permission.value for permission in unexpected),
                    )
                },
            )
        return granted


def _plugin_resource(registry: PluginRegistry, snapshot: PluginSnapshot) -> dict[str, JsonValue]:
    manifest = registry.manifest(snapshot.plugin_id)
    return {
        "id": snapshot.plugin_id,
        "type": "plugin",
        "name": manifest.name,
        "description": manifest.description,
        "author": manifest.author,
        "plugin_version": snapshot.plugin_version,
        "manifest_version": manifest.manifest_version,
        "state": snapshot.state.value,
        "compatibility": snapshot.compatibility.value,
        "health": snapshot.health.value,
        "health_detail": snapshot.health_detail,
        "configured": snapshot.configured,
        "configuration_version": snapshot.configuration_version,
        "state_version": snapshot.state_version,
        "capabilities": list(manifest.capabilities),
        "extension_ids": list(snapshot.extension_ids),
        "extension_types": list(snapshot.extension_types),
        "requested_permissions": list(snapshot.requested_permissions),
        "granted_permissions": list(snapshot.granted_permissions),
        "dependencies": list(snapshot.dependencies),
        "install_source": snapshot.install_source,
        "provenance_source": snapshot.provenance_source,
        "provenance_license": snapshot.provenance_license,
        "manifest_digest": _manifest_digest(manifest),
        "manifest": _manifest_document(manifest),
    }


def _candidate_resource(candidate: DiscoveredPlugin) -> dict[str, JsonValue]:
    manifest = candidate.manifest
    return {
        "id": manifest.plugin_id,
        "type": "plugin-candidate",
        "name": manifest.name,
        "description": manifest.description,
        "author": manifest.author,
        "plugin_version": manifest.plugin_version,
        "manifest_version": manifest.manifest_version,
        "install_source": candidate.install_source,
        "capabilities": list(manifest.capabilities),
        "requested_permissions": cast(
            JsonValue,
            sorted(permission.value for permission in manifest.requested_permissions),
        ),
        "extension_ids": [extension.extension_id for extension in manifest.extensions],
        "extension_types": [extension.extension_type.value for extension in manifest.extensions],
        "manifest_digest": _manifest_digest(manifest),
        "manifest": _manifest_document(manifest),
    }


def _manifest_document(manifest: PluginManifest) -> dict[str, JsonValue]:
    provenance: dict[str, JsonValue] = {
        "source": manifest.provenance.source,
        "license": manifest.provenance.license,
        "source_repository": manifest.provenance.source_repository,
        "revision": manifest.provenance.revision,
        "checksum": manifest.provenance.checksum,
        "trust_source": manifest.provenance.trust_source,
        "local_modifications": manifest.provenance.local_modifications,
    }
    extensions: list[JsonValue] = [
        {
            "extension_id": extension.extension_id,
            "extension_type": extension.extension_type.value,
            "interface_version": extension.interface_version,
            "entrypoint": extension.entrypoint,
            "metadata": deepcopy(extension.metadata),
        }
        for extension in manifest.extensions
    ]
    dependencies: list[JsonValue] = [
        {
            "plugin_id": dependency.plugin_id,
            "version_range": {
                "minimum": dependency.version_range.minimum,
                "maximum": dependency.version_range.maximum,
            },
            "optional": dependency.optional,
        }
        for dependency in manifest.dependencies
    ]
    migrations: list[JsonValue] = [
        {
            "migration_id": migration.migration_id,
            "from_version": migration.from_version,
            "to_version": migration.to_version,
        }
        for migration in manifest.state_migrations
    ]
    return {
        "plugin_id": manifest.plugin_id,
        "name": manifest.name,
        "description": manifest.description,
        "plugin_version": manifest.plugin_version,
        "manifest_version": manifest.manifest_version,
        "author": manifest.author,
        "provenance": provenance,
        "supported_platform": {
            "minimum": manifest.supported_platform.minimum,
            "maximum": manifest.supported_platform.maximum,
        },
        "extensions": extensions,
        "capabilities": list(manifest.capabilities),
        "requested_permissions": cast(
            JsonValue,
            sorted(permission.value for permission in manifest.requested_permissions),
        ),
        "configuration_version": manifest.configuration_version,
        "configuration_schema": deepcopy(manifest.configuration_schema),
        "dependencies": dependencies,
        "optional_external_services": list(manifest.optional_external_services),
        "state_version": manifest.state_version,
        "state_migrations": migrations,
        "ui_metadata": deepcopy(manifest.ui_metadata),
    }


def _manifest_digest(manifest: PluginManifest) -> str:
    encoded = json.dumps(
        _manifest_document(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_manifest_digest(manifest: PluginManifest, expected: str) -> None:
    actual = _manifest_digest(manifest)
    if actual != expected:
        raise ContractError(
            ErrorCode.CONFLICT,
            "plugin manifest changed since it was inspected",
            details={"expected_manifest_digest": expected, "actual_manifest_digest": actual},
        )


def _required_string(payload: dict[str, JsonValue], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{field} must be a non-blank string",
            details={"field": field},
        )
    return value


def _require_only(payload: dict[str, JsonValue], allowed: set[str]) -> None:
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "plugin command contains unsupported fields",
            details={"fields": cast(JsonValue, unexpected)},
        )
