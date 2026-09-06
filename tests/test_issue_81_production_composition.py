from __future__ import annotations

import json

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.distribution import (
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PIN_COMMAND,
    REGISTRY_PREVIEW_COMMAND,
    REGISTRY_UNPIN_COMMAND,
)


def test_default_single_node_keeps_registry_and_plugin_runtime_absent_when_unconfigured(
    tmp_path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "without-registry", secure_cookie=False)
    )

    assert REGISTRY_COLLECTION not in deployment.control_plane.registered_collections
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
    assert "plugins" in deployment.control_plane.registered_collections
    assert deployment.control_plane.plugin_registry is not None
    assert {
        REGISTRY_PREVIEW_COMMAND,
        REGISTRY_ACTIVATE_COMMAND,
        REGISTRY_PIN_COMMAND,
        REGISTRY_UNPIN_COMMAND,
        "plugin.configure",
        "plugin.disable",
        "plugin.remove",
    }.issubset(set(deployment.control_plane.registered_commands))
