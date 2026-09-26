"""Curated starter Registry shipped with the single-node product.

The browser-first setup must be useful on a clean install without requiring an operator to
preconfigure a filesystem Registry.  This catalog is deliberately conservative: it contains only
reviewed optional packages whose owner lifecycle and executable implementation are already governed
by this repository.

Installing a package is not the same as configuring or enabling its runtime.  Catalog copy must
therefore state external runtime and post-install configuration requirements explicitly.
"""

from __future__ import annotations

import json
from hashlib import sha256

from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryManifestReference,
    RegistryMaturity,
    RegistrySource,
    TrustStatus,
    VersionRange,
)
from ai_multi_agent_platform.plugins import (
    PluginManifest,
    plugin_manifest_to_document,
    reference_manifest,
)

from .hermes_plugin import hermes_plugin_manifest

_PLATFORM_REPOSITORY = "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"


def _artifact(manifest: PluginManifest) -> bytes:
    return json.dumps(
        plugin_manifest_to_document(manifest),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _platform_range(manifest: PluginManifest) -> VersionRange:
    return VersionRange(
        minimum=manifest.supported_platform.minimum,
        maximum=manifest.supported_platform.maximum,
    )


def build_starter_registry_provider() -> LocalRegistryProvider:
    """Return the reviewed offline starter catalog used by the default product profile."""

    hermes_manifest = hermes_plugin_manifest()
    hermes_artifact = _artifact(hermes_manifest)
    hermes_item = RegistryItem(
        item_id=hermes_manifest.plugin_id,
        item_type=RegistryItemType.ORCHESTRATOR,
        name="Hermes adapter",
        description=(
            "Optional Hermes orchestrator adapter for the platform. Installing this package does "
            "not install or start Hermes itself; a separately running self-hosted Hermes API "
            "server is required before the adapter can be configured and enabled in Plugins."
        ),
        version=hermes_manifest.plugin_version,
        publisher="ScoreSymphony",
        source=RegistrySource(
            repository=_PLATFORM_REPOSITORY,
            package_reference=(
                f"bundled:{hermes_manifest.plugin_id}@{hermes_manifest.plugin_version}"
            ),
        ),
        license=hermes_manifest.provenance.license,
        provenance="platform-curated bundled Hermes orchestrator adapter",
        supported_platform=_platform_range(hermes_manifest),
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
        maturity=RegistryMaturity.BETA,
        trust_status=TrustStatus.REVIEWED,
        integrity=ArtifactIntegrity(sha256=sha256(hermes_artifact).hexdigest()),
        changelog="Bundled reviewed Hermes adapter package for the default starter Registry.",
        manifest=RegistryManifestReference(
            kind=RegistryItemType.ORCHESTRATOR,
            reference="bundled:hermes-orchestrator-plugin",
            schema_version="1",
        ),
    )

    reference_manifest_value = reference_manifest()
    reference_artifact = _artifact(reference_manifest_value)
    reference_item = RegistryItem(
        item_id=reference_manifest_value.plugin_id,
        item_type=RegistryItemType.CAPABILITY_PROVIDER,
        name="Reference echo capability provider",
        description=(
            "Bundled deterministic local capability provider exposing the plugin.echo tool. "
            "Installation records the governed Plugin package only; configure and enable it in "
            "Plugins to register the capability. It requires no external service, network access, "
            "credential, paid provider, or additional system runtime."
        ),
        version=reference_manifest_value.plugin_version,
        publisher="ScoreSymphony",
        source=RegistrySource(
            repository=_PLATFORM_REPOSITORY,
            package_reference=(
                "bundled:"
                f"{reference_manifest_value.plugin_id}@{reference_manifest_value.plugin_version}"
            ),
        ),
        license=reference_manifest_value.provenance.license,
        provenance="platform-curated bundled reference capability provider",
        supported_platform=_platform_range(reference_manifest_value),
        tags=frozenset({"local-first", "offline", "deterministic", "configuration:optional"}),
        categories=frozenset({"tools"}),
        maturity=RegistryMaturity.BETA,
        trust_status=TrustStatus.REVIEWED,
        integrity=ArtifactIntegrity(sha256=sha256(reference_artifact).hexdigest()),
        changelog=(
            "Bundled reference capability provider promoted to the default starter Registry."
        ),
        manifest=RegistryManifestReference(
            kind=RegistryItemType.CAPABILITY_PROVIDER,
            reference="bundled:reference-capability-plugin",
            schema_version="1",
        ),
    )

    return LocalRegistryProvider(
        (hermes_item, reference_item),
        {
            (hermes_item.item_id, hermes_item.version): hermes_artifact,
            (reference_item.item_id, reference_item.version): reference_artifact,
        },
        provider_id="platform-starter",
    )


__all__ = ["build_starter_registry_provider"]
