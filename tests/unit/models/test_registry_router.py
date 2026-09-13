from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
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

CTX = OperationContext(correlation_id="corr-issue-10")


class LocalProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="local-openai-compatible",
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


class RemoteProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="remote-provider",
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


class UnavailableProvider(FakeModelProvider):
    descriptor = ProviderDescriptor(
        provider_id="unavailable-provider",
        provider_type="model",
        supported_operations=("generate",),
        capabilities=(
            Capability(
                name="model.text",
                kind=CapabilityKind.MODEL,
                supported_operations=("generate",),
            ),
        ),
        health=HealthStatus.UNAVAILABLE,
        available=False,
    )


def model_config(
    config_id: str,
    provider_id: str,
    *,
    aliases: tuple[str, ...] = (),
    location: ModelLocation = ModelLocation.REMOTE,
    context_window: int = 32_768,
    tool_calling: bool = False,
    structured_output: bool = False,
    streaming: bool = True,
    priority: int = 0,
) -> ModelConfiguration:
    return ModelConfiguration(
        config_id=config_id,
        display_name=config_id,
        provider_id=provider_id,
        aliases=aliases,
        location=location,
        capabilities=ModelCapabilities(
            context_window=context_window,
            tool_calling=tool_calling,
            structured_output=structured_output,
            streaming=streaming,
            modalities=("text",),
        ),
        health=HealthStatus.HEALTHY,
        priority=priority,
        adapter_metadata=(
            AdapterMetadata(
                namespace="openai-compatible",
                values={"model": f"native/{config_id}"},
            ),
        ),
    )


def test_registry_owns_canonical_ids_aliases_and_enable_state() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    config = model_config(
        "model-local-general",
        "local-openai-compatible",
        aliases=("general",),
        location=ModelLocation.LOCAL,
    )

    registered = registry.register_model(config)

    assert registered.config_id == "model-local-general"
    assert registry.get_model("general") is registered
    assert not hasattr(registered, "provider_native_model_id")
    assert registered.adapter_metadata[0].values["model"] == "native/model-local-general"

    disabled = registry.set_enabled("general", False)
    assert disabled.enabled is False
    assert disabled.revision == 2
    assert registry.get_model("model-local-general") == disabled


def test_registry_duplicate_and_alias_conflicts_are_deterministic() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    first = model_config(
        "model-a",
        "local-openai-compatible",
        aliases=("shared",),
        location=ModelLocation.LOCAL,
    )
    registry.register_model(first)
    assert registry.register_model(first) is first

    with pytest.raises(ContractError) as duplicate:
        registry.register_model(
            model_config(
                "model-a",
                "local-openai-compatible",
                aliases=("changed",),
                location=ModelLocation.LOCAL,
            )
        )
    assert duplicate.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as alias_conflict:
        registry.register_model(
            model_config(
                "model-b",
                "local-openai-compatible",
                aliases=("shared",),
                location=ModelLocation.LOCAL,
            )
        )
    assert alias_conflict.value.code is ErrorCode.CONFLICT


def test_provider_removal_does_not_destroy_canonical_model_configuration() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    config = model_config(
        "model-stable",
        "local-openai-compatible",
        location=ModelLocation.LOCAL,
    )
    registry.register_model(config)
    router = DeterministicModelRouter(registry)

    registry.unregister_provider("local-openai-compatible")
    assert registry.get_model("model-stable") == config

    with pytest.raises(ContractError) as no_route:
        router.route(RoutingRequirements(local_only=True))
    assert no_route.value.code is ErrorCode.NO_COMPATIBLE_ROUTE

    registry.register_provider(LocalProvider(response_text="replacement"))
    route = router.route(RoutingRequirements(local_only=True))
    assert route.model_config_id == "model-stable"


def test_router_filters_capabilities_location_health_and_uses_stable_priority() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    registry.register_provider(RemoteProvider())
    registry.register_provider(UnavailableProvider())

    registry.register_model(
        model_config(
            "model-local-tools",
            "local-openai-compatible",
            location=ModelLocation.LOCAL,
            tool_calling=True,
            structured_output=True,
            priority=50,
        )
    )
    registry.register_model(
        model_config(
            "model-remote-high-priority",
            "remote-provider",
            location=ModelLocation.REMOTE,
            tool_calling=True,
            structured_output=True,
            priority=100,
        )
    )
    registry.register_model(
        model_config(
            "model-unavailable",
            "unavailable-provider",
            tool_calling=True,
            structured_output=True,
            priority=999,
        )
    )
    router = DeterministicModelRouter(registry)

    unrestricted = router.route(RoutingRequirements(tool_calling=True, structured_output=True))
    local = router.route(
        RoutingRequirements(local_only=True, tool_calling=True, structured_output=True)
    )

    assert unrestricted.model_config_id == "model-remote-high-priority"
    assert unrestricted.candidate_ids == (
        "model-remote-high-priority",
        "model-local-tools",
    )
    assert local.model_config_id == "model-local-tools"
    assert "model-unavailable" not in unrestricted.candidate_ids


def test_explicit_assignment_is_honored_but_never_bypasses_requirements() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    registry.register_model(
        model_config(
            "model-explicit",
            "local-openai-compatible",
            aliases=("explicit",),
            location=ModelLocation.LOCAL,
            tool_calling=False,
        )
    )
    router = DeterministicModelRouter(registry)

    route = router.route(RoutingRequirements(explicit_model_id="explicit", local_only=True))
    assert route.model_config_id == "model-explicit"
    assert route.reason == "explicit canonical model assignment"

    with pytest.raises(ContractError) as mismatch:
        router.route(
            RoutingRequirements(
                explicit_model_id="model-explicit",
                local_only=True,
                tool_calling=True,
            )
        )
    assert mismatch.value.code is ErrorCode.NO_COMPATIBLE_ROUTE


def test_router_parses_canonical_request_requirements_and_preserves_correlation_metadata() -> None:
    registry = ModelRegistry()
    registry.register_provider(LocalProvider())
    registry.register_model(
        model_config(
            "model-json-tools",
            "local-openai-compatible",
            aliases=("agent-default",),
            location=ModelLocation.LOCAL,
            context_window=65_536,
            tool_calling=True,
            structured_output=True,
            priority=10,
        )
    )
    router = DeterministicModelRouter(registry)
    request = ModelRequest(
        request_id="req-issue-10",
        messages=("route this request",),
        context=CTX,
        requirements={
            "model_config_id": "agent-default",
            "min_context_window": 32_000,
            "tool_calling": True,
            "structured_output": True,
            "streaming": True,
            "modalities": ["text"],
            "local_only": True,
        },
    )

    selection = asyncio.run(router.select_provider(request))

    assert selection.provider_id == "local-openai-compatible"
    assert selection.model_ref == "model-json-tools"
    assert selection.adapter_metadata[0].namespace == "platform-model-router"
    assert selection.adapter_metadata[0].values["correlation_id"] == "corr-issue-10"
