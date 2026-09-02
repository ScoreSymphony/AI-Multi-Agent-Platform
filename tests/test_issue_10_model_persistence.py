from __future__ import annotations

from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import AdapterMetadata, ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    JsonModelRegistryStore,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def persisted_model() -> ModelConfiguration:
    return ModelConfiguration(
        config_id="model-persisted",
        display_name="Persisted local model",
        provider_id="provider-persisted",
        aliases=("persisted-default",),
        location=ModelLocation.SELF_HOSTED,
        node_ref="node-runtime-a",
        health=HealthStatus.HEALTHY,
        enabled=True,
        priority=17,
        capabilities=ModelCapabilities(
            context_window=65_536,
            tool_calling=True,
            structured_output=True,
            streaming=True,
            modalities=("text",),
            reasoning=("reasoning",),
        ),
        resource_hints={"vram_gb": 24},
        cost_metadata={"currency": "none", "recurring_paid_api": False},
        adapter_metadata=(
            AdapterMetadata(
                namespace="openai-compatible",
                values={"model": "native/persisted-model"},
            ),
        ),
    )


def test_registry_can_restore_inventory_before_runtime_provider_exists(tmp_path: Path) -> None:
    source = ModelRegistry()
    model = persisted_model()
    source.register_model(model)
    store = JsonModelRegistryStore(tmp_path / "models.json")
    store.save(source)

    restored_registry = ModelRegistry()
    restored = store.restore(restored_registry)

    assert restored == (model,)
    assert restored_registry.get_model("persisted-default") == model

    router = DeterministicModelRouter(restored_registry)
    with pytest.raises(ContractError) as captured:
        router.route(RoutingRequirements(self_hosted_only=True, tool_calling=True))
    assert captured.value.code is ErrorCode.NO_COMPATIBLE_ROUTE


def test_attaching_provider_after_restore_reactivates_existing_canonical_model(
    tmp_path: Path,
) -> None:
    source = ModelRegistry()
    source.register_model(persisted_model())
    store = JsonModelRegistryStore(tmp_path / "models.json")
    store.save(source)

    restored_registry = ModelRegistry()
    store.restore(restored_registry)

    provider = FakeModelProvider(model_ref="provider-native-name")
    provider.descriptor = provider.descriptor.__class__(
        provider_id="provider-persisted",
        provider_type=provider.descriptor.provider_type,
        contract_version=provider.descriptor.contract_version,
        supported_operations=provider.descriptor.supported_operations,
        capabilities=provider.descriptor.capabilities,
        health=HealthStatus.HEALTHY,
        available=True,
        limits=provider.descriptor.limits,
        resources=provider.descriptor.resources,
        adapter_metadata=provider.descriptor.adapter_metadata,
    )
    restored_registry.register_provider(provider)

    route = DeterministicModelRouter(restored_registry).route(
        RoutingRequirements(self_hosted_only=True, tool_calling=True)
    )
    assert route.model_config_id == "model-persisted"
    assert route.provider_id == "provider-persisted"


def test_persistence_is_deterministic_and_preserves_namespaced_metadata(tmp_path: Path) -> None:
    registry = ModelRegistry()
    registry.register_model(persisted_model())
    path = tmp_path / "nested" / "models.json"
    store = JsonModelRegistryStore(path)

    store.save(registry)
    first = path.read_text(encoding="utf-8")
    store.save(registry)
    second = path.read_text(encoding="utf-8")

    assert first == second
    restored = store.load_models()[0]
    assert restored.adapter_metadata[0].namespace == "openai-compatible"
    assert restored.adapter_metadata[0].values["model"] == "native/persisted-model"
    assert restored.capabilities.context_window == 65_536
    assert restored.resource_hints["vram_gb"] == 24


def test_unknown_registry_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text('{"schema_version":"999","models":[]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported model registry schema version"):
        JsonModelRegistryStore(path).load_models()
