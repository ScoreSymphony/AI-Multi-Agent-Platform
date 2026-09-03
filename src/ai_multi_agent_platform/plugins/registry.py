"""Canonical in-process plugin registry and lifecycle manager."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    PLUGIN_MANIFEST_VERSION,
    CompatibilityState,
    ExtensionType,
    PluginHealth,
    PluginManifest,
    PluginPermission,
    PluginSnapshot,
    PluginState,
)
from .runtime import ExtensionBinder, ExtensionRegistration, PluginContext, PluginRuntime


@dataclass(slots=True)
class _PluginRecord:
    manifest: PluginManifest
    state: PluginState = PluginState.INSTALLED
    compatibility: CompatibilityState = CompatibilityState.COMPATIBLE
    health: PluginHealth = PluginHealth.UNKNOWN
    health_detail: str | None = None
    configuration: dict[str, JsonValue] = field(default_factory=dict)
    configured: bool = False
    granted_permissions: frozenset[PluginPermission] = frozenset()
    runtime: PluginRuntime | None = None
    extensions: tuple[ExtensionRegistration, ...] = ()


class PluginRegistry:
    """Manage optional plugins without making their implementations part of platform core."""

    def __init__(
        self,
        *,
        platform_version: str,
        supported_interfaces: Mapping[ExtensionType, frozenset[str]],
        binders: Mapping[ExtensionType, ExtensionBinder] | None = None,
        canonical_reference_guard: Callable[[str], bool] | None = None,
    ) -> None:
        self._platform_version = platform_version
        self._supported_interfaces = dict(supported_interfaces)
        self._binders = dict(binders or {})
        self._reference_guard = canonical_reference_guard
        self._plugins: dict[str, _PluginRecord] = {}
        self._extension_owners: dict[str, str] = {}

    def install(self, manifest: PluginManifest) -> PluginSnapshot:
        if manifest.plugin_id in self._plugins:
            raise ContractError(
                ErrorCode.CONFLICT, f"plugin {manifest.plugin_id!r} is already installed"
            )
        self._validate_compatibility(manifest)
        self._plugins[manifest.plugin_id] = _PluginRecord(manifest=deepcopy(manifest))
        return self.get(manifest.plugin_id)

    def configure(self, plugin_id: str, configuration: dict[str, JsonValue]) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.state is PluginState.ENABLED:
            raise ContractError(ErrorCode.CONFLICT, "disable plugin before changing configuration")
        try:
            Draft202012Validator(record.manifest.configuration_schema).validate(configuration)
        except ValidationError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid configuration for plugin {plugin_id!r}: {exc.message}",
            ) from exc
        record.configuration = deepcopy(configuration)
        record.configured = True
        record.state = PluginState.CONFIGURED
        return self.get(plugin_id)

    async def enable(
        self,
        plugin_id: str,
        runtime: PluginRuntime,
        *,
        granted_permissions: frozenset[PluginPermission] = frozenset(),
    ) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.state is PluginState.ENABLED:
            return self.get(plugin_id)
        self._validate_compatibility(record.manifest)
        self._validate_dependencies(record.manifest)
        missing_permissions = record.manifest.requested_permissions - granted_permissions
        if missing_permissions:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"plugin {plugin_id!r} is missing granted permissions",
                details={
                    "missing_permissions": cast(
                        JsonValue,
                        sorted(permission.value for permission in missing_permissions),
                    )
                },
            )

        context = PluginContext(
            configuration=deepcopy(record.configuration),
            granted_permissions=granted_permissions,
        )
        registered: list[ExtensionRegistration] = []
        try:
            extensions = await runtime.initialize(context)
            self._validate_runtime_extensions(record.manifest, extensions)
            for extension in extensions:
                owner = self._extension_owners.get(extension.spec.extension_id)
                if owner is not None and owner != plugin_id:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        (
                            f"extension {extension.spec.extension_id!r} is already provided "
                            f"by {owner!r}"
                        ),
                    )
            for extension in extensions:
                binder = self._binders.get(extension.spec.extension_type)
                if binder is not None:
                    await binder.register(extension)
                registered.append(extension)
        except Exception:
            for extension in reversed(registered):
                binder = self._binders.get(extension.spec.extension_type)
                if binder is not None:
                    try:
                        await binder.unregister(extension)
                    except Exception:
                        pass
            try:
                await runtime.shutdown()
            except Exception:
                pass
            record.state = PluginState.FAILED
            record.health = PluginHealth.UNAVAILABLE
            raise

        for extension in extensions:
            self._extension_owners[extension.spec.extension_id] = plugin_id
        record.runtime = runtime
        record.extensions = extensions
        record.granted_permissions = granted_permissions
        record.state = PluginState.ENABLED
        report = await runtime.health()
        record.health = report.health
        record.health_detail = report.detail
        return self.get(plugin_id)

    async def disable(self, plugin_id: str) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.state is not PluginState.ENABLED:
            record.state = PluginState.DISABLED
            return self.get(plugin_id)
        runtime = record.runtime
        for extension in reversed(record.extensions):
            binder = self._binders.get(extension.spec.extension_type)
            if binder is not None:
                await binder.unregister(extension)
            self._extension_owners.pop(extension.spec.extension_id, None)
        if runtime is not None:
            await runtime.shutdown()
        record.runtime = None
        record.extensions = ()
        record.state = PluginState.DISABLED
        record.health = PluginHealth.UNKNOWN
        record.health_detail = None
        return self.get(plugin_id)

    async def refresh_health(self, plugin_id: str) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.runtime is None or record.state is not PluginState.ENABLED:
            return self.get(plugin_id)
        report = await record.runtime.health()
        record.health = report.health
        record.health_detail = report.detail
        return self.get(plugin_id)

    def validate_update(self, plugin_id: str, manifest: PluginManifest) -> None:
        record = self._record(plugin_id)
        if manifest.plugin_id != record.manifest.plugin_id:
            raise ContractError(ErrorCode.CONFLICT, "plugin update cannot change plugin_id")
        self._validate_compatibility(manifest)
        if manifest.state_version != record.manifest.state_version:
            self._validate_state_migration_path(
                manifest,
                from_version=record.manifest.state_version,
                to_version=manifest.state_version,
            )

    def remove(self, plugin_id: str) -> None:
        record = self._record(plugin_id)
        if record.state is PluginState.ENABLED:
            raise ContractError(ErrorCode.CONFLICT, "disable plugin before removal")
        if self._reference_guard is not None and self._reference_guard(plugin_id):
            raise ContractError(
                ErrorCode.CONFLICT,
                f"plugin {plugin_id!r} still has canonical references",
            )
        del self._plugins[plugin_id]

    def get(self, plugin_id: str) -> PluginSnapshot:
        record = self._record(plugin_id)
        manifest = record.manifest
        return PluginSnapshot(
            plugin_id=manifest.plugin_id,
            plugin_version=manifest.plugin_version,
            state=record.state,
            compatibility=record.compatibility,
            health=record.health,
            extension_ids=tuple(extension.extension_id for extension in manifest.extensions),
            extension_types=tuple(extension.extension_type.value for extension in manifest.extensions),
            requested_permissions=tuple(
                sorted(permission.value for permission in manifest.requested_permissions)
            ),
            granted_permissions=tuple(
                sorted(permission.value for permission in record.granted_permissions)
            ),
            dependencies=tuple(dependency.plugin_id for dependency in manifest.dependencies),
            provenance_source=manifest.provenance.source,
            provenance_license=manifest.provenance.license,
            configuration_version=manifest.configuration_version,
            state_version=manifest.state_version,
            configured=record.configured,
            health_detail=record.health_detail,
        )

    def manifest(self, plugin_id: str) -> PluginManifest:
        """Return an isolated manifest copy for inspection surfaces."""

        return deepcopy(self._record(plugin_id).manifest)

    def list_plugins(self) -> tuple[PluginSnapshot, ...]:
        return tuple(self.get(plugin_id) for plugin_id in sorted(self._plugins))

    def extension_owner(self, extension_id: str) -> str | None:
        return self._extension_owners.get(extension_id)

    def _record(self, plugin_id: str) -> _PluginRecord:
        try:
            return self._plugins[plugin_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"plugin {plugin_id!r} is not installed"
            ) from exc

    def _validate_compatibility(self, manifest: PluginManifest) -> None:
        if manifest.manifest_version != PLUGIN_MANIFEST_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"unsupported plugin manifest version {manifest.manifest_version!r}",
            )
        if not manifest.supported_platform.contains(self._platform_version):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                (
                    f"plugin {manifest.plugin_id!r} is incompatible with platform "
                    f"{self._platform_version}"
                ),
            )
        for extension in manifest.extensions:
            supported = self._supported_interfaces.get(extension.extension_type, frozenset())
            if extension.interface_version not in supported:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        f"unsupported {extension.extension_type.value} interface version "
                        f"{extension.interface_version!r}"
                    ),
                )

    def _validate_dependencies(self, manifest: PluginManifest) -> None:
        for dependency in manifest.dependencies:
            dependency_record = self._plugins.get(dependency.plugin_id)
            if dependency_record is None:
                if dependency.optional:
                    continue
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    f"required plugin dependency {dependency.plugin_id!r} is not installed",
                )
            if not dependency.version_range.contains(dependency_record.manifest.plugin_version):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    f"plugin dependency {dependency.plugin_id!r} has an incompatible version",
                )
            if not dependency.optional and dependency_record.state is not PluginState.ENABLED:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    f"required plugin dependency {dependency.plugin_id!r} is not enabled",
                )

    @staticmethod
    def _validate_runtime_extensions(
        manifest: PluginManifest,
        extensions: tuple[ExtensionRegistration, ...],
    ) -> None:
        declared = {extension.extension_id: extension for extension in manifest.extensions}
        runtime_ids = [registration.spec.extension_id for registration in extensions]
        if len(set(runtime_ids)) != len(runtime_ids):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "runtime returned duplicate extension IDs"
            )
        if set(runtime_ids) != set(declared):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "runtime extensions do not match the plugin manifest",
            )
        for registration in extensions:
            if registration.spec != declared[registration.spec.extension_id]:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    (
                        f"runtime extension {registration.spec.extension_id!r} differs from "
                        "manifest declaration"
                    ),
                )

    @staticmethod
    def _validate_state_migration_path(
        manifest: PluginManifest,
        *,
        from_version: str,
        to_version: str,
    ) -> None:
        current = from_version
        visited = {current}
        while current != to_version:
            candidates = [
                migration
                for migration in manifest.state_migrations
                if migration.from_version == current
            ]
            if len(candidates) != 1:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        f"plugin {manifest.plugin_id!r} requires one deterministic state migration "
                        f"from {current!r} toward {to_version!r}"
                    ),
                )
            current = candidates[0].to_version
            if current in visited:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    f"plugin {manifest.plugin_id!r} contains a cyclic state migration path",
                )
            visited.add(current)
