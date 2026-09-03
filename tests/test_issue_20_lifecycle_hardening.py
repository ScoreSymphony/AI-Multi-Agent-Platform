from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import (
    ExtensionRegistration,
    ExtensionType,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginPermission,
    PluginRegistry,
    PluginState,
    reference_manifest,
)
from ai_multi_agent_platform.plugins.runtime import PluginContext


class _Runtime:
    def __init__(
        self,
        extensions: tuple[PluginExtensionSpec, ...],
        *,
        fail_health: bool = False,
        fail_shutdown: bool = False,
    ) -> None:
        self.extensions = extensions
        self.fail_health = fail_health
        self.fail_shutdown = fail_shutdown
        self.initialized = False
        self.shutdown_called = False

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        del context
        self.initialized = True
        return tuple(
            ExtensionRegistration(spec=extension, instance=object())
            for extension in self.extensions
        )

    async def health(self) -> PluginHealthReport:
        if self.fail_health:
            raise ContractError(ErrorCode.UNAVAILABLE, "health failed")
        return PluginHealthReport(PluginHealth.HEALTHY)

    async def shutdown(self) -> None:
        self.shutdown_called = True
        if self.fail_shutdown:
            raise ContractError(ErrorCode.PERMANENT_FAILURE, "shutdown failed")


class _RecordingBinder:
    def __init__(self, *, fail_unregister_on: str | None = None) -> None:
        self.fail_unregister_on = fail_unregister_on
        self.registered: list[str] = []

    async def register(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_id not in self.registered:
            self.registered.append(registration.spec.extension_id)

    async def unregister(self, registration: ExtensionRegistration) -> None:
        if registration.spec.extension_id == self.fail_unregister_on:
            raise ContractError(ErrorCode.PERMANENT_FAILURE, "unregister failed")
        self.registered.remove(registration.spec.extension_id)


def _registry(*, binder: _RecordingBinder | None = None) -> PluginRegistry:
    binders = {ExtensionType.CAPABILITY_PROVIDER: binder} if binder is not None else None
    return PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders=binders,
    )


def _extension(extension_id: str) -> PluginExtensionSpec:
    return PluginExtensionSpec(
        extension_id=extension_id,
        extension_type=ExtensionType.CAPABILITY_PROVIDER,
        interface_version="1.0",
        entrypoint="tests.test_issue_20_lifecycle_hardening:_Runtime",
    )


def test_install_rejects_invalid_plugin_configuration_schema() -> None:
    registry = _registry()
    manifest = replace(
        reference_manifest(),
        configuration_schema={"type": "not-a-json-schema-type"},
    )

    with pytest.raises(ContractError) as caught:
        registry.install(manifest)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert registry.list_plugins() == ()


def test_enable_validates_effective_configuration_even_without_configure_call() -> None:
    registry = _registry()
    manifest = replace(
        reference_manifest(),
        requested_permissions=frozenset(),
        configuration_schema={
            "type": "object",
            "required": ["endpoint"],
            "properties": {"endpoint": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    runtime = _Runtime(manifest.extensions)
    registry.install(manifest)

    with pytest.raises(ContractError) as caught:
        asyncio.run(registry.enable(manifest.plugin_id, runtime))

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert runtime.initialized is False
    assert registry.get(manifest.plugin_id).state is PluginState.INSTALLED


def test_registry_rejects_undeclared_permission_overgrant_without_control_plane() -> None:
    registry = _registry()
    manifest = replace(reference_manifest(), requested_permissions=frozenset())
    runtime = _Runtime(manifest.extensions)
    registry.install(manifest)

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            registry.enable(
                manifest.plugin_id,
                runtime,
                granted_permissions=frozenset({PluginPermission.NETWORK_ACCESS}),
            )
        )

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert runtime.initialized is False
    assert registry.get(manifest.plugin_id).state is PluginState.INSTALLED


def test_initial_health_exception_rolls_back_registered_extensions() -> None:
    binder = _RecordingBinder()
    registry = _registry(binder=binder)
    manifest = replace(reference_manifest(), requested_permissions=frozenset())
    runtime = _Runtime(manifest.extensions, fail_health=True)
    registry.install(manifest)

    with pytest.raises(ContractError):
        asyncio.run(registry.enable(manifest.plugin_id, runtime))

    snapshot = registry.get(manifest.plugin_id)
    assert snapshot.state is PluginState.FAILED
    assert snapshot.health is PluginHealth.UNAVAILABLE
    assert registry.extension_owner(manifest.extensions[0].extension_id) is None
    assert binder.registered == []
    assert runtime.shutdown_called is True


def test_disable_unregister_failure_restores_prior_enabled_state() -> None:
    first = _extension("capability.hardening-first")
    second = _extension("capability.hardening-second")
    manifest = replace(
        reference_manifest(),
        extensions=(first, second),
        capabilities=(),
        requested_permissions=frozenset(),
    )
    binder = _RecordingBinder(fail_unregister_on=first.extension_id)
    registry = _registry(binder=binder)
    runtime = _Runtime(manifest.extensions)
    registry.install(manifest)
    asyncio.run(registry.enable(manifest.plugin_id, runtime))

    with pytest.raises(ContractError):
        asyncio.run(registry.disable(manifest.plugin_id))

    assert registry.get(manifest.plugin_id).state is PluginState.ENABLED
    assert binder.registered == [first.extension_id, second.extension_id]
    assert registry.extension_owner(first.extension_id) == manifest.plugin_id
    assert registry.extension_owner(second.extension_id) == manifest.plugin_id
    assert runtime.shutdown_called is False


def test_disable_shutdown_failure_is_explicit_and_cleanup_can_be_retried() -> None:
    binder = _RecordingBinder()
    registry = _registry(binder=binder)
    manifest = replace(reference_manifest(), requested_permissions=frozenset())
    runtime = _Runtime(manifest.extensions, fail_shutdown=True)
    registry.install(manifest)
    asyncio.run(registry.enable(manifest.plugin_id, runtime))

    with pytest.raises(ContractError):
        asyncio.run(registry.disable(manifest.plugin_id))

    failed = registry.get(manifest.plugin_id)
    assert failed.state is PluginState.FAILED
    assert failed.health is PluginHealth.UNAVAILABLE
    assert binder.registered == [manifest.extensions[0].extension_id]
    assert registry.extension_owner(manifest.extensions[0].extension_id) == manifest.plugin_id

    runtime.fail_shutdown = False
    disabled = asyncio.run(registry.disable(manifest.plugin_id))
    assert disabled.state is PluginState.DISABLED
    assert binder.registered == []
    assert registry.extension_owner(manifest.extensions[0].extension_id) is None


def test_update_requires_configuration_version_bump_for_schema_change() -> None:
    registry = _registry()
    manifest = replace(reference_manifest(), requested_permissions=frozenset())
    registry.install(manifest)

    candidate = replace(
        manifest,
        plugin_version="1.1.0",
        configuration_schema={
            "type": "object",
            "properties": {"prefix": {"type": "string"}, "mode": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    with pytest.raises(ContractError) as caught:
        registry.validate_update(manifest.plugin_id, candidate)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_update_detects_candidate_configuration_that_requires_reconfiguration() -> None:
    registry = _registry()
    manifest = replace(reference_manifest(), requested_permissions=frozenset())
    registry.install(manifest)
    registry.configure(manifest.plugin_id, {"prefix": "current:"})

    incompatible_candidate = replace(
        manifest,
        plugin_version="1.1.0",
        configuration_version="2.0",
        configuration_schema={
            "type": "object",
            "required": ["endpoint"],
            "properties": {"endpoint": {"type": "string"}},
            "additionalProperties": False,
        },
    )

    with pytest.raises(ContractError) as caught:
        registry.validate_update(manifest.plugin_id, incompatible_candidate)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details is not None
    assert caught.value.details["requires_reconfiguration"] is True

    compatible_candidate = replace(
        manifest,
        plugin_version="1.1.0",
        configuration_version="2.0",
        configuration_schema={
            "type": "object",
            "properties": {
                "prefix": {"type": "string"},
                "mode": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    registry.validate_update(manifest.plugin_id, compatible_candidate)


def test_reference_plugin_lifecycle_has_no_distribution_layer_dependency() -> None:
    script = """
import asyncio
import sys
from ai_multi_agent_platform.plugins import PluginRegistry, ExtensionType, reference_manifest
from ai_multi_agent_platform.plugins.reference import ReferenceCapabilityPlugin
from ai_multi_agent_platform.plugins.models import PluginPermission
registry = PluginRegistry(
    platform_version='0.0.1',
    supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({'1.0'})},
)
manifest = reference_manifest()
registry.install(manifest)
asyncio.run(registry.enable(
    manifest.plugin_id,
    ReferenceCapabilityPlugin(),
    granted_permissions=frozenset({PluginPermission.CAPABILITY_REGISTRATION}),
))
distribution_prefixes = (
    'ai_multi_agent_platform.templates',
    'ai_multi_agent_platform.import_export',
    'ai_multi_agent_platform.registry_marketplace',
)
assert not any(
    name.startswith(distribution_prefixes)
    for name in sys.modules
)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
