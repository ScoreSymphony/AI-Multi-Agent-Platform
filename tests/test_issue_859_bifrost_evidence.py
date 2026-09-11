from __future__ import annotations

import json

from ai_multi_agent_platform.benchmarking.model_gateway_evaluation import (
    ModelGatewayBenchmarkReport,
    ModelGatewayBenchmarkSpec,
    ModelGatewayDelta,
    ModelGatewayTargetResult,
)
from ai_multi_agent_platform.benchmarking.model_gateway_evidence import (
    ModelGatewayTargetEvidence,
    build_model_gateway_evidence,
)
from ai_multi_agent_platform.benchmarking.models import LatencyDistribution


def test_gateway_evidence_records_reproducible_host_and_target_metadata() -> None:
    report = _report()

    payload = build_model_gateway_evidence(
        report,
        target_evidence={
            "direct": ModelGatewayTargetEvidence(
                component="direct-openai-compatible",
                component_version="vllm-0.10.0",
                component_revision="unknown",
                native_model="local-model",
                deployment_label="same-local-model",
            ),
            "bifrost": ModelGatewayTargetEvidence(
                component="bifrost-http",
                component_version="transports/v2.1.1 (core v1.8.6)",
                component_revision="c193745d2a713e9f58f021d43e138df5eb7e038a",
                native_model="vllm-local/local-model",
                deployment_label="same-local-model",
            ),
        },
    )

    environment = payload["environment"]
    assert isinstance(environment, dict)
    assert environment["python_major_minor"]
    assert "system" in environment
    assert "cpu_count" in environment
    assert "memory_total_bytes" in environment

    evidence = payload["target_evidence"]
    assert isinstance(evidence, dict)
    assert evidence["direct"]["deployment_label"] == "same-local-model"
    assert evidence["bifrost"]["component_version"] == "transports/v2.1.1 (core v1.8.6)"
    assert evidence["bifrost"]["native_model"] == "vllm-local/local-model"
    assert "base_url" not in evidence["bifrost"]
    assert "api_key" not in evidence["bifrost"]


def test_gateway_evidence_requires_metadata_for_every_benchmark_target() -> None:
    report = _report()

    try:
        build_model_gateway_evidence(
            report,
            target_evidence={
                "direct": ModelGatewayTargetEvidence(
                    component="direct-openai-compatible",
                    component_version="unknown",
                    component_revision="unknown",
                    native_model="local-model",
                    deployment_label="same-local-model",
                )
            },
        )
    except ValueError as exc:
        assert "missing targets: bifrost" in str(exc)
    else:
        raise AssertionError("incomplete target evidence must be rejected")


def test_gateway_report_fingerprints_prompt_without_serializing_prompt_text() -> None:
    secret_prompt = "private-evaluation-prompt-must-not-leak"
    payload = _report(prompt=secret_prompt).to_dict()
    rendered = json.dumps(payload, sort_keys=True)
    benchmark = payload["benchmark"]

    assert secret_prompt not in rendered
    assert isinstance(benchmark, dict)
    assert "prompt" not in benchmark
    assert benchmark["prompt_length"] == len(secret_prompt)
    assert len(benchmark["prompt_sha256"]) == 64


def _report(*, prompt: str = "Reply with the single word: ready") -> ModelGatewayBenchmarkReport:
    latency = LatencyDistribution(
        count=1,
        min_ms=1.0,
        mean_ms=1.0,
        p50_ms=1.0,
        p95_ms=1.0,
        p99_ms=1.0,
        max_ms=1.0,
    )
    direct = ModelGatewayTargetResult(
        target_name="direct",
        provider_id="direct",
        attempted_operations=1,
        successful_operations=1,
        failed_operations=0,
        duration_seconds=0.001,
        throughput_operations_per_second=1000.0,
        latency=latency,
        canonical_identity_preserved=True,
        canonical_error_counts={},
    )
    bifrost = ModelGatewayTargetResult(
        target_name="bifrost",
        provider_id="bifrost",
        attempted_operations=1,
        successful_operations=1,
        failed_operations=0,
        duration_seconds=0.002,
        throughput_operations_per_second=500.0,
        latency=latency,
        canonical_identity_preserved=True,
        canonical_error_counts={},
    )
    return ModelGatewayBenchmarkReport(
        schema_version="1.0",
        platform_version="0.0.1",
        platform_commit="fixture",
        started_at="2026-09-12T00:00:00+00:00",
        duration_seconds=0.003,
        benchmark=ModelGatewayBenchmarkSpec(
            canonical_model_id="canonical-model",
            operation_count=1,
            concurrency=1,
            warmup_operations=0,
            prompt=prompt,
        ),
        targets={"direct": direct, "bifrost": bifrost},
        comparison_to_direct={
            "bifrost": ModelGatewayDelta(
                p50_latency_delta_ms=0.0,
                p95_latency_delta_ms=0.0,
                p99_latency_delta_ms=0.0,
                throughput_ratio=0.5,
            )
        },
    )
