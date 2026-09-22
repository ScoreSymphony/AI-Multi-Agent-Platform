"""Explicit Control Plane module for the optional plugin lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import cast

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
from ai_multi_agent_platform.plugins.manifest import plugin_manifest_to_document

from .extensions import ControlPlaneModule, ResourceService
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
PLUGIN_MODULE = "plugins"

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


class PluginControlPlaneBinding:
    """Stateful command adapter whose northbound ownership is an explicit module."""

    def __init__(
        self,
        plugin_registry: PluginRegistry | None = None,
        *,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
    ) -> None:
        if plugin_catalog is not None and plugin_registry is None:
            raise ValueError("plugin_catalog requires plugin_registry")
        self._plugin_registry = plugin_registry
        self._plugin_catalog = plugin_catalog
        self._plugin_permission_resolver = plugin_permission_resolver

    @property
    def plugin_registry(self) -> PluginRegistry | None:
        return self._plugin_registry

    @property
    def plugin_catalog(self) -> PluginCatalog | None:
        return self._plugin_catalog

    def attach(
        self,
        plugin_registry: PluginRegistry,
        *,
        plugin_catalog: PluginCatalog | None = None,
        plugin_permission_resolver: PluginPermissionResolver | None = None,
    ) -> ControlPlaneModule:
        if (
            self._plugin_registry is not None
            or self._plugin_catalog is not None
            or self._plugin_permission_resolver is not None
        ):
            raise ValueError("plugin runtime is already configured")
        self._plugin_registry = plugin_registry
        self._plugin_catalog = plugin_catalog
        self._plugin_permission_resolver = plugin_permission_resolver
        return self.module()

    def module(self) -> ControlPlaneModule:
        registry = self._require_plugin_registry()
        resources: dict[str, ResourceService] = {
            PLUGIN_COLLECTION: _PluginResources(registry),
        }
        if self._plugin_catalog is not None:
            resources[PLUGIN_CANDIDATE_COLLECTION] = _PluginCandidateResources(self._plugin_catalog)
        return ControlPlaneModule(
            name=PLUGIN_MODULE,
            resource_services=resources,
            command_handlers={
                "plugin.install": self.install,
                "plugin.configure": self.configure,
                "plugin.enable": self.enable,
                "plugin.disable": self.disable,
                "plugin.refresh-health": self.refresh_health,
                "plugin.validate-update": self.validate_update,
                "plugin.remove": self.remove,
            },
        )

    async def install(
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

    async def configure(
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
        return _plugin_resource(registry, registry.configure(resource_ref, configuration))

    async def enable(
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

    async def disable(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        registry = self._require_plugin_registry()
        return _plugin_resource(registry, await registry.disable(resource_ref))

    async def refresh_health(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context
        _require_only(payload, set())
        registry = self._require_plugin_registry()
        return _plugin_resource(registry, await registry.refresh_health(resource_ref))

    async def validate_update(
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

    async def remove(
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
        "manifest": plugin_manifest_to_document(manifest),
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
        "manifest": plugin_manifest_to_document(manifest),
    }


def _manifest_document(manifest: PluginManifest) -> dict[str, JsonValue]:
    """Backward-compatible private alias for existing internal/test imports."""

    return plugin_manifest_to_document(manifest)


def _manifest_digest(manifest: PluginManifest) -> str:
    encoded = json.dumps(
        plugin_manifest_to_document(manifest),
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


__all__ = [
    "PLUGIN_CANDIDATE_COLLECTION",
    "PLUGIN_COLLECTION",
    "PLUGIN_COLLECTIONS",
    "PLUGIN_COMMANDS",
    "PLUGIN_MODULE",
    "PluginControlPlaneBinding",
    "PluginPermissionResolver",
]
