from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.adapters.hermes_plugin import hermes_plugin_manifest
from ai_multi_agent_platform.adapters.single_node_app import (
    build_default_single_node_deployment,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
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
from ai_multi_agent_platform.plugins import PluginState, ReferenceCapabilityPlugin, reference_manifest
from ai_multi_agent_platform.plugins.reference import REFERENCE_PLUGIN_ID


def test_default_single_node_exposes_curated_starter_registry(tmp_path) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "starter-registry", secure_cookie=False)
    )

    assert REGISTRY_COLLECTION in deployment.control_plane.registered_collections
    assert MARKETPLACE_KIND_COLLECTION in deployment.control_plane.registered_collections
    assert deployment.control_plane.plugin_registry is not None
    assert deployment.control_plane.plugin_catalog is not None
    candidates = deployment.control_plane.plugin_catalog.refresh()
    assert tuple(candidate.manifest for candidate in candidates) == (
        hermes_plugin_manifest(),
        reference_manifest(),
    )
    assert isinstance(
        deployment.control_plane.plugin_catalog.create_runtime(REFERENCE_PLUGIN_ID),
        ReferenceCapabilityPlugin,
    )


def test_starter_reference_provider_round_trips_through_marketplace_owner(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_default_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "starter-lifecycle", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        actor = ActorContext(
            principal_ref=admin.user_id,
            owner_type="user",
            owner_id=admin.user_id,
        )
        manifest = reference_manifest()

        installed = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="starter-reference-install",
                correlation_id="starter-reference-lifecycle",
                actor=actor,
                idempotency_key="starter-reference-install",
            ),
            MARKETPLACE_INSTALL_COMMAND,
            REFERENCE_PLUGIN_ID,
            {
                "version": manifest.plugin_version,
                "source_registry": "platform-starter",
            },
        )

        assert installed["action"] == "install"
        assert installed["status"] == "applied"
        assert deployment.control_plane.plugin_registry is not None
        snapshot = deployment.control_plane.plugin_registry.get(REFERENCE_PLUGIN_ID)
        assert snapshot.state is PluginState.INSTALLED

        removed = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="starter-reference-uninstall",
                correlation_id="starter-reference-lifecycle",
                actor=actor,
                idempotency_key="starter-reference-uninstall",
            ),
            MARKETPLACE_UNINSTALL_COMMAND,
            REFERENCE_PLUGIN_ID,
            {},
        )

        assert removed["action"] == "uninstall"
        assert removed["status"] == "applied"
        assert all(
            plugin.plugin_id != REFERENCE_PLUGIN_ID
            for plugin in deployment.control_plane.plugin_registry.list_plugins()
        )

    asyncio.run(scenario())


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
    assert tuple(candidate.manifest for candidate in candidates) == (
        hermes_plugin_manifest(),
        reference_manifest(),
    )
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
