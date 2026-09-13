from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from ai_multi_agent_platform.adapters.openai_compatible import (
    OpenAICompatibleModelProvider,
    OpenAICompatibleProviderConfig,
)
from ai_multi_agent_platform.benchmarking.model_gateway_evaluation import (
    ModelGatewayBenchmarkSpec,
    run_model_gateway_comparison,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OperationContext,
    ProviderDescriptor,
)
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)


class _FixtureProvider(ModelProvider):
    def __init__(
        self,
        provider_id: str,
        *,
        delay_seconds: float = 0.0,
        response_model_ref: str | None = None,
        fail: bool = False,
    ) -> None:
        self._provider_id = provider_id
        self._delay_seconds = delay_seconds
        self._response_model_ref = response_model_ref
        self._fail = fail

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="issue-859-fixture",
            supported_operations=("generate",),
            capabilities=(),
            health=HealthStatus.HEALTHY,
            available=True,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._fail:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "upstream failed with synthetic-secret=must-not-leak",
                provider_id=self._provider_id,
                retryable=True,
            )
        canonical_model_id = request.requirements["model_config_id"]
        assert isinstance(canonical_model_id, str)
        return ModelResponse(
            request_id=request.request_id,
            text="ready",
            model_ref=self._response_model_ref or canonical_model_id,
            usage={"input_tokens": 1, "output_tokens": 1},
        )


def test_gateway_benchmark_compares_same_canonical_model_without_gateway_types() -> None:
    spec = ModelGatewayBenchmarkSpec(
        canonical_model_id="model-local-coder",
        operation_count=4,
        concurrency=2,
        warmup_operations=0,
    )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=spec,
            providers={
                "direct": _FixtureProvider("direct", delay_seconds=0.001),
                "bifrost": _FixtureProvider("bifrost", delay_seconds=0.002),
                "litellm": _FixtureProvider("litellm", delay_seconds=0.0015),
            },
            platform_commit="fixture",
        )
    )

    assert set(report.targets) == {"direct", "bifrost", "litellm"}
    assert set(report.comparison_to_direct) == {"bifrost", "litellm"}
    assert report.targets["direct"].successful_operations == 4
    assert report.targets["bifrost"].successful_operations == 4
    assert report.targets["bifrost"].canonical_identity_preserved is True
    assert report.targets["litellm"].canonical_identity_preserved is True
    assert report.comparison_to_direct["bifrost"].throughput_ratio is not None


def test_gateway_benchmark_detects_provider_native_identity_leak() -> None:
    spec = ModelGatewayBenchmarkSpec(
        canonical_model_id="canonical-model",
        operation_count=1,
        warmup_operations=0,
    )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=spec,
            providers={
                "direct": _FixtureProvider("direct"),
                "bifrost": _FixtureProvider(
                    "bifrost",
                    response_model_ref="provider-native-model",
                ),
            },
        )
    )

    assert report.targets["direct"].canonical_identity_preserved is True
    assert report.targets["bifrost"].canonical_identity_preserved is False


def test_gateway_benchmark_records_canonical_error_category_without_secret_message() -> None:
    spec = ModelGatewayBenchmarkSpec(
        canonical_model_id="canonical-model",
        operation_count=2,
        warmup_operations=0,
    )

    report = asyncio.run(
        run_model_gateway_comparison(
            spec=spec,
            providers={
                "direct": _FixtureProvider("direct"),
                "bifrost": _FixtureProvider("bifrost", fail=True),
            },
        )
    )
    rendered = json.dumps(report.to_dict(), sort_keys=True)

    assert report.targets["bifrost"].canonical_error_counts == {"unavailable": 2}
    assert report.targets["bifrost"].throughput_operations_per_second == 0.0
    assert report.targets["bifrost"].latency.count == 0
    assert report.comparison_to_direct["bifrost"].throughput_ratio == 0.0
    assert report.comparison_to_direct["bifrost"].p50_latency_delta_ms is None
    assert report.comparison_to_direct["bifrost"].p95_latency_delta_ms is None
    assert report.comparison_to_direct["bifrost"].p99_latency_delta_ms is None
    assert "synthetic-secret" not in rendered
    assert "must-not-leak" not in rendered


def test_bifrost_can_be_absent_without_changing_platform_model_runtime_path() -> None:
    registry = ModelRegistry()
    registry.register_provider(_FixtureProvider("issue-859-direct-only"))
    registry.register_model(
        ModelConfiguration(
            config_id="issue-859-direct-model",
            display_name="Issue 859 direct-only model",
            provider_id="issue-859-direct-only",
            capabilities=ModelCapabilities(context_window=8_192),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
            priority=100,
        )
    )

    response = asyncio.run(
        ModelRuntime(registry).generate(
            ModelRequest(
                request_id="issue-859-no-bifrost",
                messages=("Reply with the single word: ready",),
                context=OperationContext(correlation_id="issue-859:no-bifrost"),
                requirements={"model_config_id": "issue-859-direct-model"},
            )
        )
    )

    assert response.text == "ready"
    assert response.model_ref == "issue-859-direct-model"
    runtime_metadata = [
        metadata
        for metadata in response.adapter_metadata
        if metadata.namespace == "platform-model-runtime"
    ]
    assert len(runtime_metadata) == 1
    assert runtime_metadata[0].values["provider_id"] == "issue-859-direct-only"
    assert runtime_metadata[0].values["model_config_id"] == "issue-859-direct-model"


def test_bifrost_provenance_keeps_gateway_candidate_optional_and_pinned() -> None:
    provenance = Path("upstream/bifrost.yaml").read_text(encoding="utf-8")

    assert "status: candidate" in provenance
    assert "transports/v2.1.1" in provenance
    assert "c193745d2a713e9f58f021d43e138df5eb7e038a" in provenance
    assert "Apache-2.0" in provenance
    assert "GHSA-w98g-5w9p-p3rc" in provenance
    assert "experimental_only" in provenance
    assert "required_for_baseline: false" in provenance


@pytest.mark.integration
@pytest.mark.performance
def test_live_direct_vs_bifrost_comparison_when_environment_is_configured() -> None:
    direct_base_url = os.getenv("BIFROST_EVAL_DIRECT_BASE_URL")
    direct_model = os.getenv("BIFROST_EVAL_DIRECT_MODEL")
    bifrost_base_url = os.getenv("BIFROST_EVAL_BIFROST_BASE_URL")
    bifrost_model = os.getenv("BIFROST_EVAL_BIFROST_MODEL")
    if not all((direct_base_url, direct_model, bifrost_base_url, bifrost_model)):
        pytest.skip("live #859 direct/Bifrost endpoints are not configured")

    canonical_model_id = "issue-859-live-model"
    direct = _openai_provider(
        provider_id="issue-859-live-direct",
        base_url=direct_base_url,
        native_model=direct_model,
        canonical_model_id=canonical_model_id,
        api_key_env=os.getenv("BIFROST_EVAL_DIRECT_API_KEY_ENV"),
    )
    bifrost = _openai_provider(
        provider_id="issue-859-live-bifrost",
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
