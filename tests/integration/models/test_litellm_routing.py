from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.adapters.litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelRequest,
    OperationContext,
)
from ai_multi_agent_platform.models import (
    DeterministicModelRouter,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)

CTX = OperationContext(correlation_id="corr-litellm-routing")


async def completion(**kwargs: object) -> object:
    del kwargs
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ]
    }


def make_provider(provider_id: str, model_id: str) -> LiteLLMModelProvider:
    return LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id=provider_id,
            mode=LiteLLMMode.LIBRARY,
            models={model_id: f"ollama/{model_id}"},
        ),
        completion=completion,
    )


def test_platform_router_preserves_local_only_policy_and_alias_resolution() -> None:
    registry = ModelRegistry()
    local_provider = make_provider("litellm-local", "model-local")
    remote_provider = make_provider("litellm-remote", "model-remote")
    registry.register_provider(local_provider)
    registry.register_provider(remote_provider)
    asyncio.run(registry.refresh_health())

    registry.register_model(
        ModelConfiguration(
            config_id="model-local",
            display_name="Local model",
            provider_id="litellm-local",
            aliases=("local-coder",),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=10,
        )
    )
    registry.register_model(
        ModelConfiguration(
            config_id="model-remote",
            display_name="Remote model",
            provider_id="litellm-remote",
            aliases=("remote-coder",),
            location=ModelLocation.REMOTE,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )

    router = DeterministicModelRouter(registry)
    selection = asyncio.run(
        router.select_provider(
            ModelRequest(
                request_id="req-local-policy",
                messages=("hello",),
                context=CTX,
                requirements={"local_only": True},
            )
        )
    )

    assert selection.model_ref == "model-local"
    assert selection.provider_id == "litellm-local"

    alias_selection = asyncio.run(
        router.select_provider(
            ModelRequest(
                request_id="req-local-alias",
                messages=("hello",),
                context=CTX,
                requirements={
                    "model_config_id": "local-coder",
                    "local_only": True,
                },
            )
        )
    )

    assert alias_selection.model_ref == "model-local"
    assert alias_selection.provider_id == "litellm-local"

    with pytest.raises(ContractError) as captured:
        asyncio.run(
            router.select_provider(
                ModelRequest(
                    request_id="req-remote-alias-blocked",
                    messages=("hello",),
                    context=CTX,
                    requirements={
                        "model_config_id": "remote-coder",
                        "local_only": True,
                    },
                )
            )
        )

    assert captured.value.code is ErrorCode.NO_COMPATIBLE_ROUTE
    assert captured.value.provider_id == "litellm-remote"
