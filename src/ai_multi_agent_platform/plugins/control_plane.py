"""Canonical Control Plane resources and commands for issue #20 plugin lifecycle."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import CommandHandler, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

from .discovery import DiscoveredPlugin, PluginCatalog
from .models import PluginManifest, PluginPermission, PluginSnapshot
from .registry import PluginRegistry

PLUGIN_COLLECTION = "plugins"
PLUGIN_CANDIDATE_COLLECTION = "plugin-candidates"

PLUGIN_DISCOVER_COMMAND = "plugin.discover"
PLUGIN_INSTALL_COMMAND = "plugin.install"
PLUGIN_CONFIGURE_COMMAND = "plugin.configure"
PLUGIN_ENABLE_COMMAND = "plugin.enable"
PLUGIN_DISABLE_COMMAND = "plugin.disable"
PLUGIN_REFRESH_HEALTH_COMMAND = "plugin.refresh-health"
PLUGIN_VALIDATE_UPDATE_COMMAND = "plugin.validate-update"
PLUGIN_REMOVE_COMMAND = "plugin.remove"

PLUGIN_COMMANDS = (
    PLUGIN_DISCOVER_COMMAND,
    PLUGIN_INSTALL_COMMAND,
    PLUGIN_CONFIGURE_COMMAND,
    PLUGIN_ENABLE_COMMAND,
    PLUGIN_DISABLE_COMMAND,
    PLUGIN_REFRESH_HEALTH_COMMAND,
    PLUGIN_VALIDATE_UPDATE_COMMAND,
    PLUGIN_REMOVE_COMMAND,
)

type PluginPermissionGrantResolver = Callable[
    [RequestContext, PluginManifest], Awaitable[frozenset[PluginPermission]]
]


class PluginResourceService:
    """Read-only public projection of installed plugin registry state."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(
            _installed_plugin_resource(snapshot, self._registry.manifest(snapshot.plugin_id))
            for snapshot in self._registry.list_plugins()
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        snapshot = self._registry.get(resource_id)
        return _installed_plugin_resource(snapshot, self._registry.manifest(resource_id))


class PluginCandidateResourceService:
    """Read-only projection of explicitly discovered plugin candidates."""

    def __init__(self, catalog: PluginCatalog) -> None:
        self._catalog = catalog

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_candidate_resource(candidate) for candidate in self._catalog.list_candidates())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return _candidate_resource(self._catalog.candidate(resource_id))


@dataclass(frozen=True, slots=True)
class _IdempotentResult:
    command: str
    resource_ref: str
    payload_digest: str
    result: dict[str, JsonValue]


class PluginLifecycleCommands:
    """Mutating plugin lifecycle handlers behind the canonical Control Plane command seam."""

    def __init__(
        self,
        registry: PluginRegistry,
        catalog: PluginCatalog,
        *,
        permission_grants: PluginPermissionGrantResolver | None = None,
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._permission_grants = permission_grants
        self._results: dict[str, _IdempotentResult] = {}

    def handlers(self) -> dict[str, CommandHandler]:
        return {
            PLUGIN_DISCOVER_COMMAND: self.discover,
            PLUGIN_INSTALL_COMMAND: self.install,
            PLUGIN_CONFIGURE_COMMAND: self.configure,
            PLUGIN_ENABLE_COMMAND: self.enable,
            PLUGIN_DISABLE_COMMAND: self.disable,
            PLUGIN_REFRESH_HEALTH_COMMAND: self.refresh_health,
            PLUGIN_VALIDATE_UPDATE_COMMAND: self.validate_update,
            PLUGIN_REMOVE_COMMAND: self.remove,
        }

    async def discover(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if resource_ref != PLUGIN_COLLECTION:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{PLUGIN_DISCOVER_COMMAND} resource_ref must be {PLUGIN_COLLECTION!r}",
            )
        _require_empty_payload(payload, PLUGIN_DISCOVER_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            candidates = self._catalog.refresh()
            return {
                "id": PLUGIN_COLLECTION,
                "type": "plugin-discovery-result",
                "count": len(candidates),
                "candidate_ids": [candidate.manifest.plugin_id for candidate in candidates],
            }

        return await self._idempotent(
            context,
            PLUGIN_DISCOVER_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def install(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_INSTALL_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            snapshot = self._catalog.install(resource_ref, self._registry)
            return _installed_plugin_resource(snapshot, self._registry.manifest(resource_ref))

        return await self._idempotent(
            context,
            PLUGIN_INSTALL_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def configure(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        configuration = _configuration_payload(payload)

        async def operation() -> dict[str, JsonValue]:
            snapshot = self._registry.configure(resource_ref, configuration)
            return _installed_plugin_resource(snapshot, self._registry.manifest(resource_ref))

        return await self._idempotent(
            context,
            PLUGIN_CONFIGURE_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def enable(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_ENABLE_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            manifest = self._registry.manifest(resource_ref)
            grants = frozenset[PluginPermission]()
            if self._permission_grants is not None:
                grants = await self._permission_grants(context, manifest)
            unexpected = grants - manifest.requested_permissions
            if unexpected:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "plugin permission resolver granted undeclared permissions",
                    details={
                        "unexpected_permissions": sorted(
                            permission.value for permission in unexpected
                        )
                    },
                )
            runtime = self._catalog.create_runtime(resource_ref)
            snapshot = await self._registry.enable(
                resource_ref,
                runtime,
                granted_permissions=grants,
            )
            return _installed_plugin_resource(snapshot, manifest)

        return await self._idempotent(
            context,
            PLUGIN_ENABLE_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def disable(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_DISABLE_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            snapshot = await self._registry.disable(resource_ref)
            return _installed_plugin_resource(snapshot, self._registry.manifest(resource_ref))

        return await self._idempotent(
            context,
            PLUGIN_DISABLE_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def refresh_health(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_REFRESH_HEALTH_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            snapshot = await self._registry.refresh_health(resource_ref)
            return _installed_plugin_resource(snapshot, self._registry.manifest(resource_ref))

        return await self._idempotent(
            context,
            PLUGIN_REFRESH_HEALTH_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def validate_update(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_VALIDATE_UPDATE_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            installed = self._registry.manifest(resource_ref)
            candidate = self._catalog.candidate(resource_ref).manifest
            self._registry.validate_update(resource_ref, candidate)
            return {
                "id": resource_ref,
                "type": "plugin-update-validation",
                "compatible": True,
                "installed_version": installed.plugin_version,
                "candidate_version": candidate.plugin_version,
                "installed_state_version": installed.state_version,
                "candidate_state_version": candidate.state_version,
                "installed_configuration_version": installed.configuration_version,
                "candidate_configuration_version": candidate.configuration_version,
            }

        return await self._idempotent(
            context,
            PLUGIN_VALIDATE_UPDATE_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def remove(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _require_empty_payload(payload, PLUGIN_REMOVE_COMMAND)

        async def operation() -> dict[str, JsonValue]:
            self._registry.remove(resource_ref)
            return {
                "id": resource_ref,
                "type": "plugin-removal-result",
                "removed": True,
            }

        return await self._idempotent(
            context,
            PLUGIN_REMOVE_COMMAND,
            resource_ref,
            payload,
            operation,
        )

    async def _idempotent(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        payload: dict[str, JsonValue],
        operation: Callable[[], Awaitable[dict[str, JsonValue]]],
    ) -> dict[str, JsonValue]:
        key = context.idempotency_key
        if key is None or not key.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Idempotency-Key is required for plugin lifecycle commands",
                details={"header": "Idempotency-Key"},
            )
        digest = _command_payload_digest(command, resource_ref, payload)
        previous = self._results.get(key)
        if previous is not None:
            if (
                previous.command != command
                or previous.resource_ref != resource_ref
                or previous.payload_digest != digest
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Idempotency-Key was already used for a different plugin lifecycle action",
                    details={"idempotency_key": key},
                )
            return deepcopy(previous.result)

        result = await operation()
        stored = _IdempotentResult(command, resource_ref, digest, deepcopy(result))
        self._results[key] = stored
        return deepcopy(result)


def plugin_resource_services(
    registry: PluginRegistry,
    catalog: PluginCatalog,
) -> dict[str, ResourceService]:
    """Return canonical plugin collections for explicit Control Plane composition."""

    return {
        PLUGIN_COLLECTION: PluginResourceService(registry),
        PLUGIN_CANDIDATE_COLLECTION: PluginCandidateResourceService(catalog),
    }


def plugin_command_handlers(
    registry: PluginRegistry,
    catalog: PluginCatalog,
    *,
    permission_grants: PluginPermissionGrantResolver | None = None,
) -> dict[str, CommandHandler]:
    """Return canonical lifecycle commands for explicit Control Plane composition."""

    return PluginLifecycleCommands(
        registry,
        catalog,
        permission_grants=permission_grants,
    ).handlers()


def _installed_plugin_resource(
    snapshot: PluginSnapshot,
    manifest: PluginManifest,
) -> dict[str, JsonValue]:
    return {
        "id": snapshot.plugin_id,
        "type": "plugin",
        "name": manifest.name,
        "description": manifest.description,
        "author": manifest.author,
        "version": snapshot.plugin_version,
        "manifest_version": manifest.manifest_version,
        "state": snapshot.state.value,
        "compatibility": snapshot.compatibility.value,
        "health": snapshot.health.value,
        "health_detail": snapshot.health_detail,
        "extension_ids": list(snapshot.extension_ids),
        "extension_types": list(snapshot.extension_types),
        "requested_permissions": list(snapshot.requested_permissions),
        "granted_permissions": list(snapshot.granted_permissions),
        "dependencies": list(snapshot.dependencies),
        "provenance_source": snapshot.provenance_source,
        "provenance_license": snapshot.provenance_license,
        "install_source": snapshot.install_source,
        "configuration_version": snapshot.configuration_version,
        "configuration_schema": deepcopy(manifest.configuration_schema),
        "state_version": snapshot.state_version,
        "configured": snapshot.configured,
        "ui_metadata": deepcopy(manifest.ui_metadata),
    }


def _candidate_resource(candidate: DiscoveredPlugin) -> dict[str, JsonValue]:
    manifest = candidate.manifest
    extensions: list[JsonValue] = [
        {
            "id": extension.extension_id,
            "type": extension.extension_type.value,
            "interface_version": extension.interface_version,
            "entrypoint": extension.entrypoint,
            "metadata": deepcopy(extension.metadata),
        }
        for extension in manifest.extensions
    ]
    dependencies: list[JsonValue] = [
        {
            "plugin_id": dependency.plugin_id,
            "minimum_version": dependency.version_range.minimum,
            "maximum_version": dependency.version_range.maximum,
            "optional": dependency.optional,
        }
        for dependency in manifest.dependencies
    ]
    migrations: list[JsonValue] = [
        {
            "id": migration.migration_id,
            "from_version": migration.from_version,
            "to_version": migration.to_version,
        }
        for migration in manifest.state_migrations
    ]
    return {
        "id": manifest.plugin_id,
        "type": "plugin-candidate",
        "name": manifest.name,
        "description": manifest.description,
        "version": manifest.plugin_version,
        "manifest_version": manifest.manifest_version,
        "author": manifest.author,
        "install_source": candidate.install_source,
        "provenance": {
            "source": manifest.provenance.source,
            "license": manifest.provenance.license,
            "source_repository": manifest.provenance.source_repository,
            "revision": manifest.provenance.revision,
            "checksum": manifest.provenance.checksum,
            "trust_source": manifest.provenance.trust_source,
            "local_modifications": manifest.provenance.local_modifications,
        },
        "supported_platform": {
            "minimum": manifest.supported_platform.minimum,
            "maximum": manifest.supported_platform.maximum,
        },
        "extensions": extensions,
        "requested_permissions": sorted(
            permission.value for permission in manifest.requested_permissions
        ),
        "configuration_version": manifest.configuration_version,
        "configuration_schema": deepcopy(manifest.configuration_schema),
        "dependencies": dependencies,
        "optional_external_services": list(manifest.optional_external_services),
        "state_version": manifest.state_version,
        "state_migrations": migrations,
        "ui_metadata": deepcopy(manifest.ui_metadata),
    }


def _configuration_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if set(payload) != {"configuration"}:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "plugin.configure requires exactly one configuration object",
            details={"field": "configuration"},
        )
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "plugin configuration must be a JSON object",
            details={"field": "configuration"},
        )
    return deepcopy(configuration)


def _require_empty_payload(payload: dict[str, JsonValue], command: str) -> None:
    if payload:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{command} does not accept client-supplied payload fields",
            details={"fields": sorted(payload)},
        )


def _command_payload_digest(
    command: str,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> str:
    encoded = json.dumps(
        {"command": command, "resource_ref": resource_ref, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
