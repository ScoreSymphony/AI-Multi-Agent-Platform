"""Curated starter Registry shipped with the single-node product.

The browser-first setup must be useful on a clean install without requiring an operator to
preconfigure a filesystem Registry.  This catalog is intentionally tiny and conservative: it only
contains reviewed optional packages that are already shipped and governed by this repository.

Installing an adapter package is not the same as installing or starting an upstream service.  Item
copy must therefore state external runtime requirements explicitly.
"""

from __future__ import annotations

import json

from ai_multi_agent_platform.distribution import (
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistrySource,
    TrustStatus,
)
from ai_multi_agent_platform.plugins import plugin_manifest_to_document

from .hermes_plugin import hermes_plugin_manifest


def build_starter_registry_provider() -> LocalRegistryProvider:
    """Return the reviewed offline starter catalog used by the default product profile."""

    hermes_manifest = hermes_plugin_manifest()
    hermes_item = RegistryItem(
        item_id=hermes_manifest.plugin_id,
        item_type=RegistryItemType.ORCHESTRATOR,
        name="Hermes adapter",
        description=(
            "Optional Hermes orchestrator adapter for the platform. Installing this package does "
            "not install or start Hermes itself; a separately running self-hosted Hermes API "
            "server is required before the adapter can be configured and enabled."
        ),
        version=hermes_manifest.plugin_version,
        publisher="ScoreSymphony",
        source=RegistrySource(
            repository="https://github.com/ScoreSymphony/AI-Multi-Agent-Platform",
            package_reference=(
                f"bundled:{hermes_manifest.plugin_id}@{hermes_manifest.plugin_version}"
            ),
        ),
        license=hermes_manifest.provenance.license,
        provenance="platform-curated bundled Hermes orchestrator adapter",
        tags=frozenset(
            {
                "lifecycle:unknown",
                "evaluation:not-required",
                "deployment:service",
                "cost:compatible",
                "network:required",
                "setup:external-runtime-required",
            }
        ),
        categories=frozenset({"agent-framework"}),
        trust_status=TrustStatus.REVIEWED,
        manifest=RegistryManifestReference(
            kind=RegistryItemType.ORCHESTRATOR,
            reference="bundled:hermes-orchestrator-plugin",
            schema_version="1",
        ),
    )
    artifact = json.dumps(
        plugin_manifest_to_document(hermes_manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return LocalRegistryProvider(
        (hermes_item,),
        {(hermes_item.item_id, hermes_item.version): artifact},
        provider_id="platform-starter",
    )


__all__ = ["build_starter_registry_provider"]
