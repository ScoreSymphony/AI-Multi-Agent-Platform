from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.adapters.single_node_app import (
    build_default_single_node_deployment,
)
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.plugin_api import _manifest_document
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.distribution import (
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PIN_COMMAND,
    REGISTRY_PREVIEW_COMMAND,
    REGISTRY_UNPIN_COMMAND,
)
from ai_multi_agent_platform.plugins import reference_manifest


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


def test_configured_single_node_composes_manifest_backed_tool_owner_handler(tmp_path) -> None:
    manifest = reference_manifest()
    artifact = json.dumps(_manifest_document(manifest), sort_keys=True).encode("utf-8")
    artifact_path = tmp_path / "tool-manifest.json"
    artifact_path.write_bytes(artifact)
    catalog = tmp_path / "tool-catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "provider_id": "local-test",
                "items": [
                    {
                        "metadata": {
                            "schema_version": "3",
                            "item_id": manifest.plugin_id,
                            "item_type": "tool",
                            "name": manifest.name,
                            "description": manifest.description,
                            "version": manifest.plugin_version,
                            "publisher": manifest.author,
                            "source": {
                                "repository": (
                                    manifest.provenance.source_repository
                                    or "https://example.invalid/tool"
                                ),
                                "package_reference": (
                                    f"{manifest.plugin_id}@{manifest.plugin_version}"
                                ),
                            },
                            "license": manifest.provenance.license,
                            "provenance": "registry-release",
                            "supported_platform": {},
                            "dependencies": [],
                            "requested_permissions": [],
                            "required_capabilities": [],
                            "required_plugins": [],
                            "required_connectors": [],
                            "required_models": [],
                            "integrity": {},
                            "trust_status": "reviewed",
                            "deprecated": False,
                            "yanked": False,
                            "manifest": {
                                "kind": "tool",
                                "reference": artifact_path.name,
                                "schema_version": "1",
                            },
                        },
                        "artifact": artifact_path.name,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(
            data_dir=tmp_path / "with-tool-registry",
            secure_cookie=False,
            registry_catalog=catalog,
        )
    )
    handler = deployment.control_plane._command_handlers[REGISTRY_PREVIEW_COMMAND]

    preview = asyncio.run(
        handler(
            RequestContext("marketplace-tool-preview", "marketplace-tool-preview"),
            manifest.plugin_id,
            {"version": manifest.plugin_version},
        )
    )

    assert preview["route"] == "kind_handler"
    assert preview["activation_allowed"] is True
