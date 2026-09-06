from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.connectors import ReferenceConnectorProvider
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.plugin_api import _manifest_document
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.distribution import (
    ArtifactIntegrity,
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    REGISTRY_PREVIEW_COMMAND,
    RegistryItem,
    RegistryItemType,
    RegistryPluginReconciliationError,
    RegistryQuery,
    RegistryResourceService,
    RegistrySource,
    TrustStatus,
    ValidationContext,
    VersionRange,
    registry_item_from_document,
)
from ai_multi_agent_platform.plugins import PluginState, reference_manifest


def _registry_metadata(
    *,
    item_id: str,
    item_type: str,
    name: str,
    description: str,
    version: str,
    publisher: str,
    repository: str,
    package_reference: str,
    license_name: str,
    provenance: str,
    artifact: bytes,
    required_connectors: list[str] | None = None,
    tags: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1",
        "item_id": item_id,
        "item_type": item_type,
        "name": name,
        "description": description,
        "version": version,
        "publisher": publisher,
        "source": {
            "repository": repository,
            "package_reference": package_reference,
            "revision": "rev-1",
        },
        "license": license_name,
        "provenance": provenance,
        "supported_platform": {"minimum": __version__, "maximum": __version__},
        "dependencies": [],
        "requested_permissions": [],
        "required_capabilities": [],
        "required_plugins": [],
        "required_connectors": required_connectors or [],
        "required_models": [],
        "tags": tags or [],
        "categories": categories or [],
        "integrity": {
            "sha256": hashlib.sha256(artifact).hexdigest(),
            "signature": None,
            "signature_key_id": None,
        },
        "trust_status": "reviewed",
        "review_reference": None,
        "released_at": None,
        "changelog": "Hardening fixture",
        "deprecated": False,
        "yanked": False,
    }


def _write_catalog(tmp_path, metadata: dict[str, object], artifact: bytes) -> tuple[object, object]:
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_bytes(artifact)
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "provider_id": "local-test",
                "items": [{"metadata": metadata, "artifact": artifact_path.name}],
            }
        ),
        encoding="utf-8",
    )
    return catalog_path, artifact_path


def test_registry_plugin_owner_is_rehydrated_from_durable_installation(tmp_path) -> None:
    manifest = reference_manifest()
    artifact = json.dumps(_manifest_document(manifest), sort_keys=True).encode("utf-8")
    metadata = _registry_metadata(
        item_id=manifest.plugin_id,
        item_type="plugin",
        name=manifest.name,
        description=manifest.description,
        version=manifest.plugin_version,
        publisher=manifest.author,
        repository=manifest.provenance.source_repository or "https://example.invalid/plugin",
        package_reference=f"{manifest.plugin_id}@{manifest.plugin_version}",
        license_name=manifest.provenance.license,
        provenance="registry-release",
        artifact=artifact,
    )
    catalog, _ = _write_catalog(tmp_path, metadata, artifact)
    item = registry_item_from_document(metadata)
    data_dir = tmp_path / "data"
    JsonRegistryInstallationStore(data_dir / "db" / "registry-installations.json").record(
        item,
        provider_id="local-test",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    config = SingleNodeConfig(
        data_dir=data_dir,
        secure_cookie=False,
        registry_catalog=catalog,
    )

    first = build_default_single_node_deployment(config)
    first_registry = first.control_plane.plugin_registry
    assert first_registry is not None
    first_snapshot = first_registry.get(manifest.plugin_id)
    assert first_snapshot.plugin_version == manifest.plugin_version
    assert first_snapshot.state is PluginState.INSTALLED
    assert first_snapshot.granted_permissions == ()

    second = build_default_single_node_deployment(config)
    second_registry = second.control_plane.plugin_registry
    assert second_registry is not None
    assert second_registry is not first_registry
    restored = second_registry.get(manifest.plugin_id)
    assert restored.plugin_version == manifest.plugin_version
    assert restored.state is PluginState.INSTALLED
    assert restored.granted_permissions == ()


def test_registry_plugin_restart_reconciliation_rejects_changed_artifact(tmp_path) -> None:
    manifest = reference_manifest()
    artifact = json.dumps(_manifest_document(manifest), sort_keys=True).encode("utf-8")
    metadata = _registry_metadata(
        item_id=manifest.plugin_id,
        item_type="plugin",
        name=manifest.name,
        description=manifest.description,
        version=manifest.plugin_version,
        publisher=manifest.author,
        repository=manifest.provenance.source_repository or "https://example.invalid/plugin",
        package_reference=f"{manifest.plugin_id}@{manifest.plugin_version}",
        license_name=manifest.provenance.license,
        provenance="registry-release",
        artifact=artifact,
    )
    catalog, artifact_path = _write_catalog(tmp_path, metadata, artifact)
    item = registry_item_from_document(metadata)
    data_dir = tmp_path / "data"
    JsonRegistryInstallationStore(data_dir / "db" / "registry-installations.json").record(
        item,
        provider_id="local-test",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
    )
    artifact_path.write_bytes(b"tampered")

    with pytest.raises(RegistryPluginReconciliationError, match="artifact digest changed"):
        build_default_single_node_deployment(
            SingleNodeConfig(
                data_dir=data_dir,
                secure_cookie=False,
                registry_catalog=catalog,
            )
        )


def test_production_registry_validation_uses_live_connector_inventory(tmp_path) -> None:
    connector = ReferenceConnectorProvider()
    artifact = b"{}"
    metadata = _registry_metadata(
        item_id="example.connector-dependent",
        item_type="template",
        name="Connector dependent template",
        description="Requires the canonical reference connector",
        version="1.0.0",
        publisher="example",
        repository="https://example.invalid/template",
        package_reference="example.connector-dependent@1.0.0",
        license_name="MIT",
        provenance="registry-release",
        artifact=artifact,
        required_connectors=[connector.definition.id],
    )
    catalog, _ = _write_catalog(tmp_path, metadata, artifact)
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(
            data_dir=tmp_path / "data",
            secure_cookie=False,
            registry_catalog=catalog,
        )
    )
    handler = deployment.control_plane._command_handlers[REGISTRY_PREVIEW_COMMAND]
    context = RequestContext("request-1", "correlation-1")

    before = asyncio.run(
        handler(context, "example.connector-dependent", {"version": "1.0.0"})
    )
    assert before["activation_allowed"] is False
    assert any(
        finding["code"] == "missing_connector"
        for finding in before["findings"]
        if isinstance(finding, dict)
    )

    deployment.connector_registry.register(connector)
    after = asyncio.run(
        handler(context, "example.connector-dependent", {"version": "1.0.0"})
    )
    assert not any(
        finding["code"] == "missing_connector"
        for finding in after["findings"]
        if isinstance(finding, dict)
    )
    assert after["activation_allowed"] is True


def test_pinned_registry_item_still_exposes_newer_update_but_blocks_apply(tmp_path) -> None:
    old_payload = b"old"
    new_payload = b"new"
    old = RegistryItem(
        item_id="example.pinned",
        item_type=RegistryItemType.TEMPLATE,
        name="Pinned fixture",
        description="Pinned fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/pinned", "example.pinned@1.0.0"),
        license="MIT",
        provenance="source",
        supported_platform=VersionRange(__version__, __version__),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(old_payload).hexdigest()),
        trust_status=TrustStatus.REVIEWED,
    )
    candidate = RegistryItem(
        item_id=old.item_id,
        item_type=old.item_type,
        name=old.name,
        description=old.description,
        version="1.1.0",
        publisher=old.publisher,
        source=RegistrySource("https://example.invalid/pinned", "example.pinned@1.1.0"),
        license=old.license,
        provenance=old.provenance,
        supported_platform=old.supported_platform,
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(new_payload).hexdigest()),
        trust_status=old.trust_status,
    )
    provider = LocalRegistryProvider(
        (old, candidate),
        {
            (old.item_id, old.version): old_payload,
            (candidate.item_id, candidate.version): new_payload,
        },
    )
    store = JsonRegistryInstallationStore(tmp_path / "installations.json")
    store.record(old, provider_id=provider.provider_id, artifact_sha256=hashlib.sha256(old_payload).hexdigest())
    store.pin(old.item_id, old.version)
    distribution = DistributionService(provider, installations=store)
    resources = asyncio.run(
        RegistryResourceService(distribution).list_resources(
            object(),
            PageQuery(filters={"update_available": "true"}),
        )
    )

    assert [resource["id"] for resource in resources] == ["example.pinned@1.1.0"]
    assert resources[0]["update_available"] is True
    assert resources[0]["pinned_version"] == "1.0.0"
    assert [item.version for item in distribution.available_updates(old.item_id)] == ["1.1.0"]

    preview = distribution.preview(
        candidate.item_id,
        candidate.version,
        ValidationContext(__version__),
    )
    assert preview.activation_allowed is False
    assert any(finding.code == "version_pinned" for finding in preview.findings)


def test_local_registry_text_search_covers_publisher_category_and_license() -> None:
    payload = b"asset"
    item = RegistryItem(
        item_id="example.searchable",
        item_type=RegistryItemType.TEMPLATE,
        name="Searchable",
        description="Fixture",
        version="1.0.0",
        publisher="ScoreSymphony",
        source=RegistrySource("https://example.invalid/search", "example.searchable@1.0.0"),
        license="Apache-2.0",
        provenance="source",
        supported_platform=VersionRange(__version__, __version__),
        categories=frozenset({"music-research"}),
        integrity=ArtifactIntegrity(sha256=hashlib.sha256(payload).hexdigest()),
        trust_status=TrustStatus.REVIEWED,
    )
    provider = LocalRegistryProvider((item,), {(item.item_id, item.version): payload})

    for text in ("scoresymphony", "music-research", "apache-2.0"):
        assert provider.search(RegistryQuery(text=text)) == (item,)
