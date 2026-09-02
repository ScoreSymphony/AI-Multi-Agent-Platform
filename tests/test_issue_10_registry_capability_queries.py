from __future__ import annotations

import pytest

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider


class CapabilityProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="capability-provider",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
                modalities=("text",),
            ),
        ),
        health=HealthStatus.HEALTHY,
        available=True,
    )


def model(
    config_id: str,
    *,
    tool_calling: bool,
    reasoning: tuple[str, ...],
) -> ModelConfiguration:
    return ModelConfiguration(
        config_id=config_id,
        display_name=config_id,
        provider_id="capability-provider",
        location=ModelLocation.LOCAL,
        health=HealthStatus.HEALTHY,
        priority=10,
        capabilities=ModelCapabilities(
            context_window=65_536,
            tool_calling=tool_calling,
            structured_output=True,
            modalities=("text",),
            reasoning=reasoning,
        ),
    )


def test_registry_filters_backend_neutral_capabilities() -> None:
    registry = ModelRegistry()
    registry.register_provider(CapabilityProvider())
    registry.register_model(model("model-basic", tool_calling=False, reasoning=()))
    registry.register_model(
        model(
            "model-reasoning-tools",
            tool_calling=True,
            reasoning=("reasoning", "long-context-planning"),
        )
    )

    matches = registry.query_models(
        min_context_window=32_000,
        tool_calling=True,
        structured_output=True,
        modalities=("text",),
        reasoning=("reasoning",),
    )

    assert tuple(item.config_id for item in matches) == ("model-reasoning-tools",)


def test_provider_disable_makes_models_unroutable_without_deleting_inventory() -> None:
    registry = ModelRegistry()
    registry.register_provider(CapabilityProvider())
    registered = registry.register_model(
        model("model-provider-toggle", tool_calling=True, reasoning=("reasoning",))
    )
    router = DeterministicModelRouter(registry)

    route = router.route(RoutingRequirements(local_only=True, tool_calling=True))
    assert route.model_config_id == "model-provider-toggle"

    registry.set_provider_enabled("capability-provider", False)
    assert registry.provider_enabled("capability-provider") is False
    assert registry.get_model("model-provider-toggle") == registered
    assert registry.provider_health("capability-provider") is HealthStatus.UNAVAILABLE

    with pytest.raises(ContractError) as captured:
        router.route(RoutingRequirements(local_only=True, tool_calling=True))
    assert captured.value.code is ErrorCode.NO_COMPATIBLE_ROUTE

    registry.set_provider_enabled("capability-provider", True)
    restored = router.route(RoutingRequirements(local_only=True, tool_calling=True))
    assert restored.model_config_id == "model-provider-toggle"


def test_reasoning_metadata_is_part_of_deterministic_routing() -> None:
    registry = ModelRegistry()
    registry.register_provider(CapabilityProvider())
    registry.register_model(model("model-basic", tool_calling=True, reasoning=()))
    registry.register_model(
        model("model-reasoning", tool_calling=True, reasoning=("reasoning",))
    )

    route = DeterministicModelRouter(registry).route(
        RoutingRequirements(local_only=True, reasoning=("reasoning",))
    )

    assert route.model_config_id == "model-reasoning"
