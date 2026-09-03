from __future__ import annotations

import asyncio
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.plugins import (
    DiscoveredPlugin,
    InMemoryPluginStateStore,
    PluginCatalog,
    PluginOwnedState,
    PluginRegistry,
    PluginStateMigrationSpec,
    PluginStateMigrator,
    ReferenceCapabilityPlugin,
    StaticPluginSource,
    reference_manifest,
    validate_manifest_document,
)
from ai_multi_agent_platform.plugins.reference import REFERENCE_CAPABILITY_ID


def _registry() -> PluginRegistry:
    return PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={
            reference_manifest().extensions[0].extension_type: frozenset({"1.0"})
        },
    )


class _Migration:
    def __init__(
        self,
        spec: PluginStateMigrationSpec,
        *,
        marker: str,
        fail: bool = False,
    ) -> None:
        self._spec = spec
        self._marker = marker
        self._fail = fail

    @property
    def spec(self) -> PluginStateMigrationSpec:
        return self._spec

    async def migrate(self, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if self._fail:
            raise ContractError(ErrorCode.PERMANENT_FAILURE, "migration failed")
        migrated = deepcopy(payload)
        migrated[self._marker] = True
        return migrated


def _valid_manifest_document() -> dict[str, object]:
    return {
        "plugin_id": "example.plugin",
        "name": "Example",
        "description": "Example plugin",
        "plugin_version": "1.0.0",
        "manifest_version": "1",
        "author": "tests",
        "provenance": {"source": "test", "license": "MIT"},
        "supported_platform": {"minimum": "0.0.1", "maximum": "0.0.1"},
        "extensions": [
            {
                "extension_id": "capability.example",
                "extension_type": "capability_provider",
                "interface_version": "1.0",
                "entrypoint": "example:Runtime",
                "metadata": {},
            }
        ],
        "requested_permissions": ["capability_registration"],
        "configuration_version": "1.0",
        "configuration_schema": {"type": "object"},
        "dependencies": [],
        "optional_external_services": [],
        "state_version": "2.0",
        "state_migrations": [
            {
                "migration_id": "state.1-to-2",
                "from_version": "1.0",
                "to_version": "2.0",
            }
        ],
        "ui_metadata": {},
    }


def test_manifest_schema_validates_versioned_state_and_extension_metadata() -> None:
    validate_manifest_document(_valid_manifest_document())

    invalid = _valid_manifest_document()
    extensions = invalid["extensions"]
    assert isinstance(extensions, list)
    first = extensions[0]
    assert isinstance(first, dict)
    first["extension_type"] = "plugin_private_shortcut"

    with pytest.raises(ContractError) as caught:
        validate_manifest_document(invalid)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_discovery_uses_explicit_sources_without_importing_manifest_entrypoints() -> None:
    extension = replace(
        reference_manifest().extensions[0],
        entrypoint="definitely_missing_plugin.module:Runtime",
    )
    manifest = replace(reference_manifest(), extensions=(extension,))
    candidate = DiscoveredPlugin(
        manifest=manifest,
        runtime_factory=ReferenceCapabilityPlugin,
        install_source="bundled:test-fixture",
    )
    catalog = PluginCatalog(StaticPluginSource(candidate))

    assert "definitely_missing_plugin.module" not in sys.modules
    discovered = catalog.refresh()
    assert discovered[0].manifest.plugin_id == manifest.plugin_id
    assert "definitely_missing_plugin.module" not in sys.modules

    snapshot = catalog.install(manifest.plugin_id, _registry())
    assert snapshot.install_source == "bundled:test-fixture"
    assert isinstance(catalog.create_runtime(manifest.plugin_id), ReferenceCapabilityPlugin)
    assert "definitely_missing_plugin.module" not in sys.modules


def test_discovery_rejects_duplicate_plugin_ids_across_sources() -> None:
    candidate = DiscoveredPlugin(
        manifest=reference_manifest(),
        runtime_factory=ReferenceCapabilityPlugin,
        install_source="source:a",
    )
    duplicate = replace(candidate, install_source="source:b")
    catalog = PluginCatalog(StaticPluginSource(candidate), StaticPluginSource(duplicate))

    with pytest.raises(ContractError) as caught:
        catalog.refresh()
    assert caught.value.code is ErrorCode.CONFLICT


def test_registry_tracks_metadata_and_keeps_manifest_metadata_isolated() -> None:
    manifest = reference_manifest()
    registry = _registry()
    snapshot = registry.install(manifest, install_source="trusted:bundle")

    assert snapshot.extension_types == ("capability_provider",)
    assert snapshot.dependencies == ()
    assert snapshot.provenance_source == "bundled-reference"
    assert snapshot.provenance_license == "MIT"
    assert snapshot.install_source == "trusted:bundle"
    assert snapshot.configuration_version == "1.0"
    assert snapshot.state_version == "1.0"

    manifest.extensions[0].metadata["capability_id"] = "mutated.original"
    isolated = registry.manifest(manifest.plugin_id)
    assert isolated.extensions[0].metadata["capability_id"] == REFERENCE_CAPABILITY_ID

    isolated.extensions[0].metadata["capability_id"] = "mutated.copy"
    fresh = registry.manifest(manifest.plugin_id)
    assert fresh.extensions[0].metadata["capability_id"] == REFERENCE_CAPABILITY_ID


def test_disable_before_configuration_does_not_claim_plugin_is_configured() -> None:
    registry = _registry()
    manifest = reference_manifest()
    registry.install(manifest)

    snapshot = asyncio.run(registry.disable(manifest.plugin_id))
    assert snapshot.configured is False


def test_state_migration_is_versioned_and_commits_only_after_success() -> None:
    first = PluginStateMigrationSpec(
        migration_id="state.1-to-2",
        from_version="1.0",
        to_version="2.0",
    )
    second = PluginStateMigrationSpec(
        migration_id="state.2-to-3",
        from_version="2.0",
        to_version="3.0",
    )
    manifest = replace(
        reference_manifest(),
        state_version="3.0",
        state_migrations=(first, second),
    )
    store = InMemoryPluginStateStore()
    original = PluginOwnedState(
        plugin_id=manifest.plugin_id,
        state_version="1.0",
        payload={"value": 1},
    )
    asyncio.run(store.save(original))

    failing_migrator = PluginStateMigrator(
        _Migration(first, marker="first"),
        _Migration(second, marker="second", fail=True),
    )
    with pytest.raises(ContractError) as caught:
        asyncio.run(failing_migrator.migrate(manifest, store))
    assert caught.value.code is ErrorCode.PERMANENT_FAILURE
    assert asyncio.run(store.load(manifest.plugin_id)) == original

    migrator = PluginStateMigrator(
        _Migration(first, marker="first"),
        _Migration(second, marker="second"),
    )
    migrated = asyncio.run(migrator.migrate(manifest, store))
    assert migrated is not None
    assert migrated.state_version == "3.0"
    assert migrated.payload == {"value": 1, "first": True, "second": True}
    assert asyncio.run(store.load(manifest.plugin_id)) == migrated


def test_update_validation_requires_deterministic_state_migration_path() -> None:
    registry = _registry()
    manifest = reference_manifest()
    registry.install(manifest)

    incompatible_update = replace(
        manifest,
        plugin_version="1.1.0",
        state_version="2.0",
        state_migrations=(),
    )
    with pytest.raises(ContractError) as caught:
        registry.validate_update(manifest.plugin_id, incompatible_update)
    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION

    migration = PluginStateMigrationSpec(
        migration_id="state.1-to-2",
        from_version="1.0",
        to_version="2.0",
    )
    compatible_update = replace(incompatible_update, state_migrations=(migration,))
    registry.validate_update(manifest.plugin_id, compatible_update)


def test_core_package_import_does_not_load_optional_plugin_subsystem() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import ai_multi_agent_platform; "
                "assert 'ai_multi_agent_platform.plugins' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
