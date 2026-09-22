from __future__ import annotations

import json

from ai_multi_agent_platform.adapters.hermes_plugin import hermes_plugin_manifest
from ai_multi_agent_platform.adapters.single_node_app import (
    build_default_single_node_deployment,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.distribution import (
    MARKETPLACE_INSTALL_COMMAND,
    MARKETPLACE_KIND_COLLECTION,
    MARKETPLACE_PREVIEW_COMMAND,
    MARKETPLACE_UNINSTALL_COMMAND,
    MARKETPLACE_UPDATE_COMMAND,
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PIN_COMMAND,
    REGISTRY_PREVIEW_COMMAND,
    REGISTRY_UNPIN_COMMAND,
)


def test_default_single_node_exposes_curated_starter_registry(tmp_path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "starter-registry", secure_cookie=False)
    )

    assert REGISTRY_COLLECTION in deployment.control_plane.registered_collections
    assert MARKETPLACE_KIND_COLLECTION in deployment.control_plane.registered_collections
    assert deployment.control_plane.plugin_registry is not None
    assert deployment.control_plane.plugin_catalog is not None
    candidates = deployment.control_plane.plugin_catalog.refresh()
    assert tuple(candidate.manifest for candidate in candidates) == (hermes_plugin_manifest(),)


def test_single_node_can_explicitly_disable_registry_and_plugin_runtime(tmp_path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(
            data_dir=tmp_path / "without-registry",
            secure_cookie=False,
            registry_enabled=False,
        )
    )

    assert REGISTRY_COLLECTION not in deployment.control_plane.registered_collections
    assert MARKETPLACE_KIND_COLLECTION not in deployment.control_plane.registered_collections
    assert deployment.control_plane.plugin_registry is None


def test_configured_single_node_shares_registry_plugins_with_canonical_plugin_lifecycle(
    tmp_path,
) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "provider_id": "local-test",
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(
            data_dir=tmp_path / "with-registry",
            secure_cookie=False,
            registry_catalog=catalog,
        )
    )

    assert REGISTRY_COLLECTION in deployment.control_plane.registered_collections
    assert MARKETPLACE_KIND_COLLECTION in deployment.control_plane.registered_collections
    assert "plugins" in deployment.control_plane.registered_collections
    assert "plugin-candidates" in deployment.control_plane.registered_collections
    assert deployment.control_plane.plugin_registry is not None
    assert deployment.control_plane.plugin_catalog is not None
    candidates = deployment.control_plane.plugin_catalog.refresh()
    assert tuple(candidate.manifest for candidate in candidates) == (hermes_plugin_manifest(),)
    assert {
        REGISTRY_PREVIEW_COMMAND,
        REGISTRY_ACTIVATE_COMMAND,
        REGISTRY_PIN_COMMAND,
        REGISTRY_UNPIN_COMMAND,
        MARKETPLACE_PREVIEW_COMMAND,
        MARKETPLACE_INSTALL_COMMAND,
        MARKETPLACE_UPDATE_COMMAND,
        MARKETPLACE_UNINSTALL_COMMAND,
        "plugin.configure",
        "plugin.enable",
        "plugin.disable",
        "plugin.remove",
    }.issubset(set(deployment.control_plane.registered_commands))
