from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace

import pytest

from ai_multi_agent_platform.capabilities.registry import CapabilityRegistry
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import (
    CapabilityRegistryBinder,
    ExtensionRegistration,
    ExtensionType,
    PluginDependency,
    PluginExtensionSpec,
    PluginHealth,
    PluginHealthReport,
    PluginManifest,
    PluginPermission,
    PluginProvenance,
    PluginRegistry,
    PluginState,
    ReferenceCapabilityPlugin,
    VersionRange,
    reference_manifest,
    validate_manifest_document,
)
from ai_multi_agent_platform.plugins.reference import REFERENCE_CAPABILITY_ID
from ai_multi_agent_platform.plugins.runtime import PluginContext


class _Runtime:
    def __init__(
        self,
        spec: PluginExtensionSpec,
        *,
        fail_initialize: bool = False,
        health: PluginHealth = PluginHealth.HEALTHY,
    ) -> None:
        self.spec = spec
        self.fail_initialize = fail_initialize
        self.health_state = health
        self.shutdown_called = False

    async def initialize(self, context: PluginContext) -> tuple[ExtensionRegistration, ...]:
        del context
        if self.fail_initialize:
            raise ContractError(ErrorCode.PERMANENT_FAILURE, "initialization failed")
        return (ExtensionRegistration(spec=self.spec, instance=object()),)

    async def health(self) -> PluginHealthReport:
        return PluginHealthReport(self.health_state)

    async def shutdown(self) -> None:
        self.shutdown_called = True


def _extension(extension_id: str) -> PluginExtensionSpec:
    return PluginExtensionSpec(
        extension_id=extension_id,
        extension_type=ExtensionType.CAPABILITY_PROVIDER,
        interface_version="1.0",
        entrypoint="tests.test_plugins:_Runtime",
    )


def _manifest(
    plugin_id: str,
    extension_id: str,
    *,
    dependencies: tuple[PluginDependency, ...] = (),
    requested_permissions: frozenset[PluginPermission] = frozenset(),
) -> PluginManifest:
    return PluginManifest(
        plugin_id=plugin_id,
        name=plugin_id,
        description="test plugin",
        plugin_version="1.0.0",
        author="tests",
        provenance=PluginProvenance(source="test", license="MIT"),
        supported_platform=VersionRange(minimum="0.0.1", maximum="0.0.1"),
        extensions=(_extension(extension_id),),
        dependencies=dependencies,
        requested_permissions=requested_permissions,
    )


def _registry(
    *,
    binders: dict[ExtensionType, CapabilityRegistryBinder] | None = None,
    guard: Callable[[str], bool] | None = None,
) -> PluginRegistry:
    return PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
        binders=binders,
        canonical_reference_guard=guard,
    )


def test_reference_plugin_registers_disables_and_removes_cleanly() -> None:
    capability_registry = CapabilityRegistry()
    registry = _registry(
        binders={ExtensionType.CAPABILITY_PROVIDER: CapabilityRegistryBinder(capability_registry)}
    )
    registry.install(reference_manifest())
    registry.configure(reference_manifest().plugin_id, {"prefix": "plugin:"})

    enabled = asyncio.run(
        registry.enable(
            reference_manifest().plugin_id,
            ReferenceCapabilityPlugin(),
            granted_permissions=frozenset({PluginPermission.CAPABILITY_REGISTRATION}),
        )
    )

    assert enabled.state is PluginState.ENABLED
    assert enabled.health is PluginHealth.HEALTHY
    assert [item.capability_id for item in capability_registry.list_capabilities()] == [
        REFERENCE_CAPABILITY_ID
    ]

    disabled = asyncio.run(registry.disable(reference_manifest().plugin_id))
    assert disabled.state is PluginState.DISABLED
    assert capability_registry.list_capabilities() == ()
    registry.remove(reference_manifest().plugin_id)
    assert registry.list_plugins() == ()


def test_invalid_manifest_document_is_rejected() -> None:
    with pytest.raises(ContractError) as caught:
        validate_manifest_document({"plugin_id": "incomplete"})
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_incompatible_platform_and_interface_versions_fail_before_install() -> None:
    registry = _registry()
    incompatible_platform = replace(
        reference_manifest(),
        supported_platform=VersionRange(minimum="1.0.0"),
    )
    with pytest.raises(ContractError) as caught:
        registry.install(incompatible_platform)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    unsupported_extension = replace(
        reference_manifest().extensions[0],
        interface_version="2.0",
    )
    unsupported_interface = replace(reference_manifest(), extensions=(unsupported_extension,))
    with pytest.raises(ContractError) as caught:
        registry.install(unsupported_interface)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_permission_and_configuration_failures_do_not_activate_plugin() -> None:
    registry = _registry()
    manifest = reference_manifest()
    registry.install(manifest)

    with pytest.raises(ContractError) as caught:
        registry.configure(manifest.plugin_id, {"prefix": 123})
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    with pytest.raises(ContractError) as caught:
        asyncio.run(registry.enable(manifest.plugin_id, ReferenceCapabilityPlugin()))
    assert caught.value.code is ErrorCode.FORBIDDEN
    assert registry.extension_owner(manifest.extensions[0].extension_id) is None
    assert registry.get(manifest.plugin_id).state is PluginState.INSTALLED


def test_missing_dependency_and_duplicate_extension_fail_deterministically() -> None:
    registry = _registry()
    dependency = PluginDependency(plugin_id="dependency.plugin")
    consumer = _manifest(
        "consumer.plugin",
        "capability.consumer",
        dependencies=(dependency,),
    )
    registry.install(consumer)
    with pytest.raises(ContractError) as caught:
        asyncio.run(registry.enable(consumer.plugin_id, _Runtime(consumer.extensions[0])))
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    first = _manifest("first.plugin", "capability.duplicate")
    second = _manifest("second.plugin", "capability.duplicate")
    registry.install(first)
    registry.install(second)
    asyncio.run(registry.enable(first.plugin_id, _Runtime(first.extensions[0])))
    with pytest.raises(ContractError) as caught:
        asyncio.run(registry.enable(second.plugin_id, _Runtime(second.extensions[0])))
    assert caught.value.code is ErrorCode.CONFLICT
    assert registry.extension_owner("capability.duplicate") == first.plugin_id
    assert registry.get(second.plugin_id).state is PluginState.FAILED


def test_initialization_failure_is_contained_and_health_is_reported() -> None:
    registry = _registry()
    failing = _manifest("failing.plugin", "capability.failing")
    registry.install(failing)
    runtime = _Runtime(failing.extensions[0], fail_initialize=True)
    with pytest.raises(ContractError):
        asyncio.run(registry.enable(failing.plugin_id, runtime))
    assert runtime.shutdown_called
    assert registry.get(failing.plugin_id).state is PluginState.FAILED
    assert registry.extension_owner(failing.extensions[0].extension_id) is None

    degraded = _manifest("degraded.plugin", "capability.degraded")
    registry.install(degraded)
    snapshot = asyncio.run(
        registry.enable(
            degraded.plugin_id,
            _Runtime(degraded.extensions[0], health=PluginHealth.DEGRADED),
        )
    )
    assert snapshot.health is PluginHealth.DEGRADED


def test_remove_refuses_plugins_with_canonical_references() -> None:
    registry = _registry(guard=lambda plugin_id: plugin_id == "referenced.plugin")
    manifest = _manifest("referenced.plugin", "capability.referenced")
    registry.install(manifest)
    with pytest.raises(ContractError) as caught:
        registry.remove(manifest.plugin_id)
    assert caught.value.code is ErrorCode.CONFLICT


def _issue_20_manifest_document(*, extension_type: str = "transport_provider") -> dict[str, object]:
    return {
        "plugin_id": "acceptance.plugin",
        "name": "Acceptance plugin",
        "description": "Issue 20 acceptance manifest",
        "plugin_version": "1.0.0",
        "manifest_version": "1",
        "author": "tests",
        "provenance": {"source": "test", "license": "MIT"},
        "supported_platform": {"minimum": "0.0.1", "maximum": "0.0.1"},
        "extensions": [
            {
                "extension_id": "transport.acceptance",
                "extension_type": extension_type,
                "interface_version": "1.0",
                "entrypoint": "acceptance:Runtime",
                "metadata": {},
            }
        ],
        "capabilities": ["transport.messages"],
        "requested_permissions": [],
        "configuration_version": "1.0",
        "configuration_schema": {"type": "object", "additionalProperties": False},
        "dependencies": [],
        "optional_external_services": [],
        "state_version": "1.0",
        "state_migrations": [],
        "ui_metadata": {},
    }


def test_issue_20_reserves_every_required_extension_category() -> None:
    required = {
        "orchestrator",
        "executor",
        "model_provider",
        "model_routing_policy",
        "capability_provider",
        "memory_provider",
        "file_provider",
        "knowledge_provider",
        "event_provider",
        "transport_provider",
        "authorization_provider",
        "observability_exporter",
        "automation_provider",
        "evaluator",
        "node_provider",
        "worker_provider",
        "connector_provider",
        "frontend_extension",
        "configuration_extension",
    }
    assert required <= {extension.value for extension in ExtensionType}


def test_issue_20_manifest_v1_requires_explicit_capability_declarations() -> None:
    document = _issue_20_manifest_document()
    validate_manifest_document(document)

    missing = dict(document)
    del missing["capabilities"]
    with pytest.raises(ContractError) as caught:
        validate_manifest_document(missing)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_issue_20_manifest_accepts_transport_and_configuration_extensions() -> None:
    validate_manifest_document(_issue_20_manifest_document(extension_type="transport_provider"))
    validate_manifest_document(_issue_20_manifest_document(extension_type="configuration_extension"))


def test_issue_20_manifest_model_rejects_duplicate_capability_ids() -> None:
    manifest = reference_manifest()
    with pytest.raises(ValueError, match="duplicate capabilities"):
        replace(manifest, capabilities=(REFERENCE_CAPABILITY_ID, REFERENCE_CAPABILITY_ID))


def test_issue_20_reference_plugin_declares_its_capability() -> None:
    assert reference_manifest().capabilities == (REFERENCE_CAPABILITY_ID,)
