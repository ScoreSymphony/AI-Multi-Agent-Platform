from __future__ import annotations

import json

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
    RegistryQuery,
    TrustStatus,
    derive_technical_metadata,
)


def test_starter_registry_exposes_truthful_hermes_adapter_package() -> None:
    provider = build_starter_registry_provider()

    items = provider.search(RegistryQuery())

    assert provider.provider_id == "platform-starter"
    assert len(items) == 1
    item = items[0]
    assert item.item_id == HERMES_PLUGIN_ID
    assert item.version == HERMES_PLUGIN_VERSION
    assert item.item_type is RegistryItemType.ORCHESTRATOR
    assert item.name == "Hermes adapter"
    assert item.route is DistributionRoute.KIND_HANDLER
    assert item.trust_status is TrustStatus.REVIEWED
    assert item.source.repository == "https://github.com/ScoreSymphony/AI-Multi-Agent-Platform"
    assert "does not install or start Hermes itself" in item.description

    technical = derive_technical_metadata(item)
    assert technical is not None
    assert technical.deployment_modes == ("service",)
    assert technical.network_status == "required"
    assert technical.cost_status == "compatible"

    artifact = json.loads(provider.fetch_artifact(item.item_id, item.version).decode("utf-8"))
    assert artifact["plugin_id"] == HERMES_PLUGIN_ID
    assert artifact["plugin_version"] == HERMES_PLUGIN_VERSION
    assert artifact["optional_external_services"] == ["hermes-api-server"]
    assert (
        artifact["provenance"]["source_repository"]
        == "https://github.com/NousResearch/hermes-agent"
    )


def test_starter_registry_projects_hermes_external_runtime_prerequisite() -> None:
    provider = build_starter_registry_provider()
    port = DistributionSetupRegistryPort(DistributionService(provider), None)

    (item,) = port.search()

    assert item.item_id == HERMES_PLUGIN_ID
    assert item.technical is not None
    assert item.technical.external_runtime_required is True
    assert item.technical.network_status == "required"
    assert item.technical.deployment_modes == ("service",)
