from __future__ import annotations

import json
from hashlib import sha256

from ai_multi_agent_platform.adapters.hermes_plugin import (
    HERMES_PLUGIN_ID,
    HERMES_PLUGIN_VERSION,
)
from ai_multi_agent_platform.adapters.setup_registry import DistributionSetupRegistryPort
from ai_multi_agent_platform.adapters.starter_registry import build_starter_registry_provider
from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    DistributionService,
    RegistryItemType,
    RegistryMaturity,
    RegistryQuery,
    TrustStatus,
    derive_technical_metadata,
)
from ai_multi_agent_platform.plugins.reference import (
    REFERENCE_CAPABILITY_ID,
    REFERENCE_PLUGIN_ID,
)


def _items_by_id():
    provider = build_starter_registry_provider()
    items = {item.item_id: item for item in provider.search(RegistryQuery())}
    return provider, items


def test_starter_registry_exposes_curated_multi_kind_catalog() -> None:
    provider, items = _items_by_id()

    assert provider.provider_id == "platform-starter"
    assert set(items) == {HERMES_PLUGIN_ID, REFERENCE_PLUGIN_ID}
    assert {item.item_type for item in items.values()} == {
        RegistryItemType.ORCHESTRATOR,
        RegistryItemType.CAPABILITY_PROVIDER,
    }


def test_starter_registry_exposes_truthful_hermes_adapter_package() -> None:
    provider, items = _items_by_id()

    item = items[HERMES_PLUGIN_ID]
    assert item.version == HERMES_PLUGIN_VERSION
    assert item.item_type is RegistryItemType.ORCHESTRATOR
    assert item.name == "Hermes adapter"
    assert item.route is DistributionRoute.KIND_HANDLER
    assert item.trust_status is TrustStatus.REVIEWED
    assert item.maturity is RegistryMaturity.BETA
    assert item.source.repository == "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"
    assert "does not install or start Hermes itself" in item.description
    assert "configured and enabled in Plugins" in item.description

    technical = derive_technical_metadata(item)
    assert technical is not None
    assert technical.deployment_modes == ("service",)
    assert technical.network_status == "required"
    assert technical.cost_status == "compatible"

    artifact_bytes = provider.fetch_artifact(item.item_id, item.version)
    assert item.integrity.sha256 == sha256(artifact_bytes).hexdigest()
    artifact = json.loads(artifact_bytes.decode("utf-8"))
    assert artifact["plugin_id"] == HERMES_PLUGIN_ID
    assert artifact["plugin_version"] == HERMES_PLUGIN_VERSION
    assert artifact["optional_external_services"] == ["hermes-api-server"]
    assert (
        artifact["provenance"]["source_repository"]
        == "https://github.com/NousResearch/hermes-agent"
    )


def test_starter_registry_exposes_local_reference_capability_provider() -> None:
    provider, items = _items_by_id()

    item = items[REFERENCE_PLUGIN_ID]
    assert item.item_type is RegistryItemType.CAPABILITY_PROVIDER
    assert item.name == "Reference echo capability provider"
    assert item.route is DistributionRoute.KIND_HANDLER
    assert item.trust_status is TrustStatus.REVIEWED
    assert item.maturity is RegistryMaturity.BETA
    assert item.source.repository == "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"
    assert item.categories == frozenset({"tools"})
    assert "no external service" in item.description
    assert "configure and enable it in Plugins" in item.description
    assert derive_technical_metadata(item) is None

    artifact_bytes = provider.fetch_artifact(item.item_id, item.version)
    assert item.integrity.sha256 == sha256(artifact_bytes).hexdigest()
    artifact = json.loads(artifact_bytes.decode("utf-8"))
    assert artifact["plugin_id"] == REFERENCE_PLUGIN_ID
    assert artifact["optional_external_services"] == []
    assert artifact["capabilities"] == [REFERENCE_CAPABILITY_ID]
    assert artifact["extensions"][0]["extension_type"] == "capability_provider"


def test_starter_registry_projects_external_and_local_setup_requirements() -> None:
    provider = build_starter_registry_provider()
    port = DistributionSetupRegistryPort(DistributionService(provider), None)

    items = {item.item_id: item for item in port.search()}
    hermes = items[HERMES_PLUGIN_ID]
    reference = items[REFERENCE_PLUGIN_ID]

    assert hermes.technical is not None
    assert hermes.technical.external_runtime_required is True
    assert hermes.technical.network_status == "required"
    assert hermes.technical.deployment_modes == ("service",)

    assert reference.technical is None
    assert reference.route == DistributionRoute.KIND_HANDLER.value
