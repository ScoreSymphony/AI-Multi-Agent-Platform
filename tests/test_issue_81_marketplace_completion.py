from __future__ import annotations

import asyncio
import hashlib
import hmac
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import PageQuery
from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DistributionService,
    HmacSha256SignatureVerifier,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryResourceService,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    VersionRange,
)
from ai_multi_agent_platform.plugins import (
    ExtensionType,
    PluginRegistry,
    PluginStateMigrationSpec,
    reference_manifest,
)


def _item(
    version: str,
    *,
    tags: frozenset[str] = frozenset(),
    trust: TrustStatus = TrustStatus.REVIEWED,
    artifact: bytes | None = None,
    signature: str | None = None,
    signature_key_id: str | None = None,
) -> tuple[RegistryItem, bytes]:
    payload = artifact or f"asset:{version}".encode()
    return (
        RegistryItem(
            item_id="example.marketplace",
            item_type=RegistryItemType.TEMPLATE,
            name="Marketplace fixture",
            description="Issue 81 completion fixture",
            version=version,
            publisher="example",
            source=RegistrySource(
                "https://example.invalid/registry",
                f"marketplace@{version}",
                revision=f"rev-{version}",
            ),
            license="MIT",
            provenance="verified-source",
            supported_platform=VersionRange("0.0.1", "1.0.0"),
            tags=tags,
            integrity=ArtifactIntegrity(
                sha256=hashlib.sha256(payload).hexdigest(),
                signature=signature,
                signature_key_id=signature_key_id,
            ),
            trust_status=trust,
        ),
        payload,
    )


def test_installation_state_survives_restart_and_preserves_history_and_pin(tmp_path) -> None:
    path = tmp_path / "registry-installations.json"
    first, _ = _item("1.0.0")
    second, _ = _item("1.1.0")

    store = JsonRegistryInstallationStore(path)
    store.record(first, provider_id="private-registry")
    pinned = store.pin(first.item_id, first.version)
    assert pinned.pinned_version == "1.0.0"

    reloaded = JsonRegistryInstallationStore(path)
    assert reloaded.get(first.item_id) == pinned
    reloaded.unpin(first.item_id)
    updated = reloaded.record(second, provider_id="private-registry")

    assert updated.current.version == "1.1.0"
    assert [entry.version for entry in updated.history] == ["1.0.0"]
    assert updated.current.source_registry == "private-registry"
    assert updated.current.source_repository == second.source.repository
    assert updated.current.package_reference == second.source.package_reference
    assert updated.current.provenance == second.provenance

    restarted = JsonRegistryInstallationStore(path)
    assert restarted.get(first.item_id) == updated


def test_registry_resource_service_applies_domain_filters_and_update_availability(tmp_path) -> None:
    old, old_payload = _item("1.0.0", tags=frozenset({"music"}))
    candidate, candidate_payload = _item("1.1.0", tags=frozenset({"music", "analysis"}))
    unrelated_payload = b"other"
    unrelated = RegistryItem(
        item_id="example.other",
        item_type=RegistryItemType.AGENT,
        name="Other",
        description="Unrelated fixture",
        version="1.0.0",
        publisher="other",
        source=RegistrySource("https://example.invalid/other", "other@1.0.0"),
        license="Apache-2.0",
        provenance="other-source",
        supported_platform=VersionRange("0.0.1", "1.0.0"),
        tags=frozenset({"general"}),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(unrelated_payload).hexdigest()),
        trust_status=TrustStatus.UNTRUSTED,
    )
    provider = LocalRegistryProvider(
        (old, candidate, unrelated),
        {
            (old.item_id, old.version): old_payload,
            (candidate.item_id, candidate.version): candidate_payload,
            (unrelated.item_id, unrelated.version): unrelated_payload,
        },
    )
    installations = JsonRegistryInstallationStore(tmp_path / "installations.json")
    installations.record(old, provider_id=provider.provider_id)
    service = RegistryResourceService(DistributionService(provider, installations=installations))

    resources = asyncio.run(
        service.list_resources(
            object(),
            PageQuery(
                search="marketplace",
                filters={
                    "tag": "analysis",
                    "trust_status": "reviewed",
                    "update_available": "true",
                },
            ),
        )
    )

    assert [resource["id"] for resource in resources] == ["example.marketplace@1.1.0"]
    assert resources[0]["installed"] is True
    assert resources[0]["installed_version"] == "1.0.0"
    assert resources[0]["update_available"] is True


def test_signed_artifact_requires_and_accepts_authoritative_verification() -> None:
    key = b"registry-test-key"
    payload = b"signed-marketplace-artifact"
    signature = "hmac-sha256:" + hmac.new(key, payload, hashlib.sha256).hexdigest()
    item, artifact = _item(
        "1.0.0",
        artifact=payload,
        signature=signature,
        signature_key_id="publisher-key",
    )
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): artifact})

    unverified = DistributionService(provider).preview(
        item.item_id,
        item.version,
        ValidationContext("0.0.1"),
    )
    assert unverified.activation_allowed is False
    assert any(finding.code == "signature_unverified" for finding in unverified.findings)

    verified = DistributionService(
        provider,
        signature_verifier=HmacSha256SignatureVerifier({"publisher-key": key}),
    ).preview(item.item_id, item.version, ValidationContext("0.0.1"))
    assert verified.activation_allowed is True
    assert not any(finding.code.startswith("signature_") for finding in verified.findings)


def test_plugin_registry_applies_only_explicit_newer_stopped_updates() -> None:
    registry = PluginRegistry(
        platform_version="0.0.1",
        supported_interfaces={ExtensionType.CAPABILITY_PROVIDER: frozenset({"1.0"})},
    )
    original = reference_manifest()
    registry.install(original, install_source="registry:local@1.0.0")

    candidate = replace(original, plugin_version="1.1.0")
    updated = registry.apply_update(
        original.plugin_id,
        candidate,
        install_source="registry:local@1.1.0",
    )
    assert updated.plugin_version == "1.1.0"
    assert updated.install_source == "registry:local@1.1.0"
    assert updated.granted_permissions == ()

    with pytest.raises(ContractError) as same_version:
        registry.apply_update(original.plugin_id, candidate)
    assert same_version.value.code is ErrorCode.CONFLICT

    state_candidate = replace(
        candidate,
        plugin_version="1.2.0",
        state_version="2.0.0",
        state_migrations=(
            PluginStateMigrationSpec(
                migration_id="state-v2",
                from_version="1.0",
                to_version="2.0.0",
            ),
        ),
    )
    with pytest.raises(ContractError) as migration_required:
        registry.apply_update(original.plugin_id, state_candidate)
    assert migration_required.value.code is ErrorCode.UNAVAILABLE
