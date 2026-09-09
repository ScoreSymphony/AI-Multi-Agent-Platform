from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)
from ai_multi_agent_platform.testing import FakeModelProvider


def _registry(*, include_local: bool = True, remote_trust: str = "configured") -> ModelRegistry:
    registry = ModelRegistry()
    registry.register_provider(FakeModelProvider())
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote-paid",
            display_name="Remote Paid",
            provider_id="fake-model",
            capabilities=ModelCapabilities(context_window=64_000),
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
            priority=200,
            resource_hints={
                "egress_profile": {
                    "profile_id": "profile-remote-paid",
                    "revision": 4,
                    "posture": "external",
                    "cost_class": "paid_external",
                    "trust": remote_trust,
                    "policy_source": "deployment-config",
                    "source_revision": "policy-7",
                    "network_egress_required": True,
                }
            },
        )
    )
    if include_local:
        registry.register_model(
            ModelConfiguration(
                config_id="model-local-safe",
                display_name="Local Safe",
                provider_id="fake-model",
                capabilities=ModelCapabilities(context_window=32_000),
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
                priority=10,
            )
        )
    return registry


def _request(*, explicit_model_id: str | None = None) -> ModelRequest:
    requirements: dict[str, object] = {"data_classification": "confidential"}
    if explicit_model_id is not None:
        requirements["model_config_id"] = explicit_model_id
    return ModelRequest(
        request_id="model-egress-route-591",
        messages=("confidential project content",),
        context=OperationContext(correlation_id="corr-model-egress-591"),
        requirements=requirements,  # type: ignore[arg-type]
    )


def test_prohibited_high_priority_remote_model_falls_back_to_local_candidate() -> None:
    runtime = ModelRuntime(_registry())

    selection = asyncio.run(runtime.select(_request()))

    assert selection.model_ref == "model-local-safe"
    metadata = selection.adapter_metadata[0].values
    assert metadata["candidate_ids"] == ["model-remote-paid", "model-local-safe"]
    assert metadata["policy_exclusions"] == [
        {
            "model_config_id": "model-remote-paid",
            "provider_id": "fake-model",
            "reason": "paid_external_denied",
        }
    ]


def test_explicit_prohibited_remote_model_is_not_silently_replaced() -> None:
    runtime = ModelRuntime(_registry())

    with pytest.raises(ContractError) as captured:
        asyncio.run(runtime.select(_request(explicit_model_id="model-remote-paid")))

    assert captured.value.code is ErrorCode.NO_COMPATIBLE_ROUTE
    assert captured.value.details["explicit_model_id"] == "model-remote-paid"
    assert captured.value.details["policy_exclusions"] == [
        {
            "model_config_id": "model-remote-paid",
            "provider_id": "fake-model",
            "reason": "paid_external_denied",
        }
    ]


def test_all_prohibited_candidates_fail_with_structured_exclusion_reason() -> None:
    runtime = ModelRuntime(_registry(include_local=False))

    with pytest.raises(ContractError) as captured:
        asyncio.run(runtime.select(_request()))

    assert captured.value.code is ErrorCode.NO_COMPATIBLE_ROUTE
    assert captured.value.details["policy_exclusions"] == [
        {
            "model_config_id": "model-remote-paid",
            "provider_id": "fake-model",
            "reason": "paid_external_denied",
        }
    ]


def test_unverified_remote_profile_cannot_win_fallback_routing() -> None:
    runtime = ModelRuntime(_registry(remote_trust="unverified"))

    selection = asyncio.run(runtime.select(_request()))

    assert selection.model_ref == "model-local-safe"
    assert selection.adapter_metadata[0].values["policy_exclusions"] == [
        {
            "model_config_id": "model-remote-paid",
            "provider_id": "fake-model",
            "reason": "unverified_profile",
        }
    ]
