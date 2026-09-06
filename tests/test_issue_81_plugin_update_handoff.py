from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace

from ai_multi_agent_platform.control_plane.plugin_api import _manifest_document
from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    PluginRegistryArtifactInstaller,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    VersionRange,
)
from ai_multi_agent_platform.plugins import ExtensionType, PluginRegistry, reference_manifest


def test_registry_plugin_update_is_applied_by_plugin_owner() -> None:
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    original = reference_manifest()
    registry.install(original, install_source="registry:reference@1.0.0")

    candidate = replace(original, plugin_version="1.1.0")
    artifact = json.dumps(_manifest_document(candidate), sort_keys=True).encode("utf-8")
    item = RegistryItem(
        item_id=candidate.plugin_id,
        item_type=RegistryItemType.PLUGIN,
        name=candidate.name,
        description=candidate.description,
        version=candidate.plugin_version,
        publisher=candidate.author,
        source=RegistrySource(
            "https://example.invalid/plugins/reference",
            "reference.capability-plugin@1.1.0",
        ),
        license=candidate.provenance.license,
        provenance="registry-release",
        supported_platform=VersionRange("0.0.1", "0.0.1"),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(artifact).hexdigest()),
        trust_status=TrustStatus.REVIEWED,
    )

    result = asyncio.run(
        PluginRegistryArtifactInstaller(registry).install_verified_plugin(item, artifact)
    )

    assert result.plugin_version == "1.1.0"
    assert registry.get(candidate.plugin_id).plugin_version == "1.1.0"
    assert registry.get(candidate.plugin_id).install_source.endswith("@1.1.0")
