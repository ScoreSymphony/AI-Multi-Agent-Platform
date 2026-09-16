from __future__ import annotations

import asyncio
import os

import pytest

from ai_multi_agent_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.benchmarking.model_gateway_evaluation import (
    ModelGatewayBenchmarkSpec,
    run_model_gateway_comparison,
)


def test_live_direct_vs_bifrost_comparison_when_environment_is_configured() -> None:
    direct_base_url = os.getenv("BIFROST_EVAL_DIRECT_BASE_URL")
    direct_model = os.getenv("BIFROST_EVAL_DIRECT_MODEL")
    bifrost_base_url = os.getenv("BIFROST_EVAL_BIFROST_BASE_URL")
    bifrost_model = os.getenv("BIFROST_EVAL_BIFROST_MODEL")
    if not all((direct_base_url, direct_model, bifrost_base_url, bifrost_model)):
        pytest.skip("live direct/Bifrost evaluation endpoints are not configured")

    canonical_model_id = "bifrost-live-evaluation-model"
    direct = _openai_provider(
        provider_id="bifrost-live-direct",
        base_url=direct_base_url,
        native_model=direct_model,
        canonical_model_id=canonical_model_id,
        api_key_env=os.getenv("BIFROST_EVAL_DIRECT_API_KEY_ENV"),
    )
    bifrost = _openai_provider(
        provider_id="bifrost-live-gateway",
        base_url=bifrost_base_url,
        native_model=bifrost_model,
        canonical_model_id=canonical_model_id,
        api_key_env=os.getenv("BIFROST_EVAL_BIFROST_API_KEY_ENV"),
    )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=ModelGatewayBenchmarkSpec(
                canonical_model_id=canonical_model_id,
                operation_count=int(os.getenv("BIFROST_EVAL_OPERATION_COUNT", "20")),
                concurrency=int(os.getenv("BIFROST_EVAL_CONCURRENCY", "4")),
                warmup_operations=int(os.getenv("BIFROST_EVAL_WARMUP", "2")),
            ),
            providers={"direct": direct, "bifrost": bifrost},
            platform_commit=os.getenv("GITHUB_SHA", "local"),
        )
    )

    assert report.targets["direct"].failed_operations == 0
    assert report.targets["bifrost"].failed_operations == 0
    assert report.targets["direct"].canonical_identity_preserved is True
    assert report.targets["bifrost"].canonical_identity_preserved is True


def _openai_provider(
    *,
    provider_id: str,
    base_url: str | None,
    native_model: str | None,
    canonical_model_id: str,
    api_key_env: str | None,
) -> OpenAICompatibleModelProvider:
    assert base_url is not None
    assert native_model is not None
    return OpenAICompatibleModelProvider(
        OpenAICompatibleProviderConfig(
            provider_id=provider_id,
            base_url=base_url,
            models={canonical_model_id: native_model},
            api_key_env=api_key_env,
        )
    )
