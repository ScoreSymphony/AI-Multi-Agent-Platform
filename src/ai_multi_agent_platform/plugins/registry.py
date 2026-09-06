"""Canonical in-process plugin registry and lifecycle manager."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

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
    install_source: str
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

    def install(
        self,
        manifest: PluginManifest,
        *,
        install_source: str | None = None,
    ) -> PluginSnapshot:
        if manifest.plugin_id in self._plugins:
            raise ContractError(
                ErrorCode.CONFLICT, f"plugin {manifest.plugin_id!r} is already installed"
            )
        self._validate_compatibility(manifest)
        resolved_install_source = install_source or manifest.provenance.source
        if not resolved_install_source.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "plugin install source must be non-blank",
            )
        self._plugins[manifest.plugin_id] = _PluginRecord(
            manifest=deepcopy(manifest),
            install_source=resolved_install_source,
        )
        return self.get(manifest.plugin_id)

    def configure(self, plugin_id: str, configuration: dict[str, JsonValue]) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.state is PluginState.ENABLED:
            raise ContractError(ErrorCode.CONFLICT, "disable plugin before changing configuration")
        self._validate_configuration(record.manifest, configuration)
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
        if record.runtime is not None or record.extensions:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"plugin {plugin_id!r} has residual runtime state; disable it before re-enabling",
            )
        self._validate_compatibility(record.manifest)
        self._validate_configuration(record.manifest, record.configuration)
        self._validate_dependencies(record.manifest)

        unexpected_permissions = granted_permissions - record.manifest.requested_permissions
        if unexpected_permissions:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"plugin {plugin_id!r} was granted undeclared permissions",
                details={
                    "unexpected_permissions": cast(
                        JsonValue,
                        sorted(permission.value for permission in unexpected_permissions),
                    )
                },
            )

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
            report = await runtime.health()
        except Exception:
            rollback_failed = False
            for extension in reversed(registered):
                binder = self._binders.get(extension.spec.extension_type)
                if binder is not None:
                    try:
                        await binder.unregister(extension)
                    except Exception:
                        rollback_failed = True
            try:
                await runtime.shutdown()
            except Exception:
                rollback_failed = True
            record.runtime = None
            record.extensions = ()
            record.granted_permissions = frozenset()
            record.state = PluginState.FAILED
            record.health = PluginHealth.UNAVAILABLE
            record.health_detail = (
                "plugin enable failed and rollback was incomplete"
                if rollback_failed
                else "plugin enable failed and was rolled back"
            )
            raise

        for extension in extensions:
            self._extension_owners[extension.spec.extension_id] = plugin_id
        record.runtime = runtime
        record.extensions = extensions
        record.granted_permissions = granted_permissions
        record.state = PluginState.ENABLED
        record.health = report.health
        record.health_detail = report.detail
        return self.get(plugin_id)

    async def disable(self, plugin_id: str) -> PluginSnapshot:
        record = self._record(plugin_id)
        if record.runtime is None and not record.extensions:
            record.state = PluginState.DISABLED
            record.health = PluginHealth.UNKNOWN
            record.health_detail = None
            return self.get(plugin_id)

        runtime = record.runtime
        unregistered: list[ExtensionRegistration] = []
        shutdown_started = False
        try:
            for extension in reversed(record.extensions):
                binder = self._binders.get(extension.spec.extension_type)
                if binder is not None:
                    await binder.unregister(extension)
                    unregistered.append(extension)
            if runtime is not None:
                shutdown_started = True
                await runtime.shutdown()
        except Exception:
            rollback_failed = False
            for extension in reversed(unregistered):
                binder = self._binders.get(extension.spec.extension_type)
                if binder is not None:
                    try:
                        await binder.register(extension)
                    except Exception:
                        rollback_failed = True
            if shutdown_started or rollback_failed:
                record.state = PluginState.FAILED
                record.health = PluginHealth.UNAVAILABLE
                record.health_detail = (
                    "plugin disable failed after shutdown began"
                    if shutdown_started and not rollback_failed
                    else "plugin disable failed and rollback was incomplete"
                )
            raise

        for extension in record.extensions:
            self._extension_owners.pop(extension.spec.extension_id, None)
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
        try:
            report = await record.runtime.health()
        except Exception:
            record.health = PluginHealth.UNAVAILABLE
            record.health_detail = "plugin health check failed"
            raise
        record.health = report.health
        record.health_detail = report.detail
        return self.get(plugin_id)

    def validate_update(self, plugin_id: str, manifest: PluginManifest) -> None:
        record = self._record(plugin_id)
        if manifest.plugin_id != record.manifest.plugin_id:
            raise ContractError(ErrorCode.CONFLICT, "plugin update cannot change plugin_id")
        if _version_key(manifest.plugin_version) <= _version_key(record.manifest.plugin_version):
            raise ContractError(
                ErrorCode.CONFLICT,
                "plugin update version must be newer than the installed version",
            )
        self._validate_compatibility(manifest)

        if (
            manifest.configuration_schema != record.manifest.configuration_schema
            and manifest.configuration_version == record.manifest.configuration_version
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                (
                    f"plugin {plugin_id!r} changes configuration schema without changing "
                    "configuration_version"
                ),
            )

        try:
            self._validate_configuration(manifest, record.configuration)
        except ContractError as exc:
            if manifest.configuration_version != record.manifest.configuration_version:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    (
                        f"plugin {plugin_id!r} configuration version "
                        f"{manifest.configuration_version!r} requires reconfiguration before update"
                    ),
                    details={
                        "current_configuration_version": record.manifest.configuration_version,
                        "candidate_configuration_version": manifest.configuration_version,
                        "requires_reconfiguration": True,
                    },
                ) from exc
            raise

        if manifest.state_version != record.manifest.state_version:
            self._validate_state_migration_path(
                manifest,
                from_version=record.manifest.state_version,
                to_version=manifest.state_version,
            )

    def apply_update(
        self,
        plugin_id: str,
        manifest: PluginManifest,
        *,
        install_source: str | None = None,
        state_migration_applied: bool = False,
    ) -> PluginSnapshot:
        """Apply one explicitly validated plugin update while the runtime is stopped.

        Configuration is retained only when it validates against the candidate manifest. Runtime
        permission grants and health are reset so a later enable must pass authorization again.
        State-version changes require the owning deployment to run the declared migration before
        committing the new manifest.
        """

        record = self._record(plugin_id)
        self.validate_update(plugin_id, manifest)
        if record.runtime is not None or record.extensions or record.state is PluginState.ENABLED:
            raise ContractError(ErrorCode.CONFLICT, "disable plugin before applying an update")
        if manifest.state_version != record.manifest.state_version and not state_migration_applied:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "plugin state migration must complete before applying this update",
                details={
                    "current_state_version": record.manifest.state_version,
                    "candidate_state_version": manifest.state_version,
                },
            )

        resolved_install_source = install_source or manifest.provenance.source
        if not resolved_install_source.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "plugin update install source must be non-blank",
            )

        was_disabled = record.state is PluginState.DISABLED
        record.manifest = deepcopy(manifest)
        record.install_source = resolved_install_source
        record.compatibility = CompatibilityState.COMPATIBLE
        record.health = PluginHealth.UNKNOWN
        record.health_detail = None
        record.granted_permissions = frozenset()
        if was_disabled:
            record.state = PluginState.DISABLED
        elif record.configured:
            record.state = PluginState.CONFIGURED
        else:
            record.state = PluginState.INSTALLED
        return self.get(plugin_id)

    def remove(self, plugin_id: str) -> None:
        record = self._record(plugin_id)
        if record.runtime is not None or record.extensions or record.state is PluginState.ENABLED:
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
            extension_types=tuple(
                extension.extension_type.value for extension in manifest.extensions
            ),
            requested_permissions=tuple(
                sorted(permission.value for permission in manifest.requested_permissions)
            ),
            granted_permissions=tuple(
                sorted(permission.value for permission in record.granted_permissions)
            ),
            dependencies=tuple(dependency.plugin_id for dependency in manifest.dependencies),
            provenance_source=manifest.provenance.source,
            provenance_license=manifest.provenance.license,
            install_source=record.install_source,
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
        try:
            Draft202012Validator.check_schema(manifest.configuration_schema)
        except SchemaError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"plugin {manifest.plugin_id!r} contains an invalid configuration schema",
            ) from exc
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

    @staticmethod
    def _validate_configuration(
        manifest: PluginManifest,
        configuration: dict[str, JsonValue],
    ) -> None:
        try:
            Draft202012Validator(manifest.configuration_schema).validate(configuration)
        except ValidationError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid configuration for plugin {manifest.plugin_id!r}: {exc.message}",
            ) from exc

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


def _version_key(value: str) -> tuple[int, int, int]:
    parts = [int(part) for part in value.split(".")]
    parts.extend([0] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]
