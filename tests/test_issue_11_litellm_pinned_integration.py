from __future__ import annotations

import asyncio
from importlib import metadata

import litellm

from ai_multi_agent_platform.adapters.litellm import (
    LiteLLMMode,
    LiteLLMModelProvider,
    LiteLLMProviderConfig,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.models import CanonicalModelRequest, ModelMessage, ModelRole

PINNED_LITELLM_VERSION = "1.99.0"


def test_pinned_litellm_library_executes_through_platform_adapter() -> None:
    """Exercise the installed LiteLLM package, not a fake ``acompletion`` symbol.

    LiteLLM's supported ``mock_response`` path executes its real request/response
    machinery without requiring a paid provider or network credential.  The platform
    adapter still owns canonical request translation and response parsing.
    """

    assert metadata.version("litellm") == PINNED_LITELLM_VERSION

    async def pinned_completion(**kwargs: object) -> object:
        return await litellm.acompletion(
            **kwargs,
            mock_response="pinned-litellm-integration-ok",
        )

    provider = LiteLLMModelProvider(
        LiteLLMProviderConfig(
            provider_id="litellm-pinned-integration",
            mode=LiteLLMMode.LIBRARY,
            models={"model-ci": "openai/gpt-4o-mini"},
            timeout_seconds=30.0,
        ),
        completion=pinned_completion,
    )
    request = CanonicalModelRequest(
        request_id="req-litellm-pinned-integration",
        context=OperationContext(correlation_id="corr-litellm-pinned-integration"),
        messages=(ModelMessage.text(ModelRole.USER, "integration probe"),),
        model_config_id="model-ci",
    ).to_contract_request()

    response = asyncio.run(provider.generate(request))

    assert response.model_ref == "model-ci"
    assert response.content == "pinned-litellm-integration-ok"
    assert any(
        item.namespace == "litellm" and item.values.get("litellm_version") == PINNED_LITELLM_VERSION
        for item in response.adapter_metadata
    )
