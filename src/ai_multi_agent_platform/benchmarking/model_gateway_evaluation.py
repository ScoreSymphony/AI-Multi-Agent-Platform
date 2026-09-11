"""Reproducible direct-vs-gateway model benchmark evidence for issue #859.

The harness is intentionally gateway-neutral. Bifrost and LiteLLM are evaluated by
pointing ordinary platform ModelProvider implementations at their HTTP endpoints;
neither gateway becomes a canonical model or routing authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.contracts import (
    ContractError,
    ModelProvider,
    ModelRequest,
    OperationContext,
)

from .models import LatencyDistribution

MODEL_GATEWAY_EVALUATION_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class ModelGatewayBenchmarkSpec:
    """One bounded workload shared by direct and gateway-backed providers."""

    canonical_model_id: str
    operation_count: int = 20
    concurrency: int = 4
    warmup_operations: int = 2
    timeout_seconds: float = 30.0
    prompt: str = "Reply with the single word: ready"

    def __post_init__(self) -> None:
        if not self.canonical_model_id.strip():
            raise ValueError("canonical_model_id must not be empty")
        if self.operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")


@dataclass(frozen=True, slots=True)
class ModelGatewayTargetResult:
    """Secret-free evidence for one endpoint path."""

    target_name: str
    provider_id: str
    attempted_operations: int
    successful_operations: int
    failed_operations: int
    duration_seconds: float
    throughput_operations_per_second: float
    latency: LatencyDistribution
    canonical_identity_preserved: bool
    canonical_error_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["canonical_error_counts"] = dict(self.canonical_error_counts)
        return payload


@dataclass(frozen=True, slots=True)
class ModelGatewayDelta:
    """Gateway overhead relative to the direct endpoint using the same workload."""

    p50_latency_delta_ms: float
    p95_latency_delta_ms: float
    p99_latency_delta_ms: float
    throughput_ratio: float | None


@dataclass(frozen=True, slots=True)
class ModelGatewayBenchmarkReport:
    schema_version: str
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    benchmark: ModelGatewayBenchmarkSpec
    targets: Mapping[str, ModelGatewayTargetResult]
    comparison_to_direct: Mapping[str, ModelGatewayDelta]

    def to_dict(self) -> dict[str, Any]:
        benchmark = asdict(self.benchmark)
        prompt = benchmark.pop("prompt")
        if not isinstance(prompt, str):  # pragma: no cover - dataclass invariant.
            raise TypeError("benchmark prompt must be a string")
        benchmark["prompt_sha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        benchmark["prompt_length"] = len(prompt)
        return {
            "schema_version": self.schema_version,
            "platform_version": self.platform_version,
            "platform_commit": self.platform_commit,
            "started_at": self.started_at,
            "duration_seconds": self.duration_seconds,
            "benchmark": benchmark,
            "targets": {name: result.to_dict() for name, result in self.targets.items()},
            "comparison_to_direct": {
                name: asdict(delta) for name, delta in self.comparison_to_direct.items()
            },
        }


async def run_model_gateway_comparison(
    *,
    spec: ModelGatewayBenchmarkSpec,
    providers: Mapping[str, ModelProvider],
    platform_commit: str = "unknown",
) -> ModelGatewayBenchmarkReport:
    """Run an identical canonical request workload across direct/gateway paths.

    ``providers`` must contain a target named ``direct``. Other names are free-form
    (for issue #859 the expected names are ``bifrost`` and optionally ``litellm``).
    Only canonical response identity and aggregate measurements are recorded; raw
    prompts, responses, credentials, headers and exception messages are excluded.
    """

    if "direct" not in providers:
        raise ValueError("providers must contain a direct target")
    if len(providers) < 2:
        raise ValueError("at least one gateway target is required")
    if any(not name.strip() for name in providers):
        raise ValueError("target names must not be empty")

    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    results: dict[str, ModelGatewayTargetResult] = {}
    for target_name, provider in providers.items():
        results[target_name] = await _run_target(
            target_name=target_name,
            provider=provider,
            spec=spec,
        )

    direct = results["direct"]
    deltas: dict[str, ModelGatewayDelta] = {}
    for target_name, result in results.items():
        if target_name == "direct":
            continue
        throughput_ratio: float | None = None
        if direct.throughput_operations_per_second > 0:
            throughput_ratio = round(
                result.throughput_operations_per_second / direct.throughput_operations_per_second,
                6,
            )
        deltas[target_name] = ModelGatewayDelta(
            p50_latency_delta_ms=round(result.latency.p50_ms - direct.latency.p50_ms, 3),
            p95_latency_delta_ms=round(result.latency.p95_ms - direct.latency.p95_ms, 3),
            p99_latency_delta_ms=round(result.latency.p99_ms - direct.latency.p99_ms, 3),
            throughput_ratio=throughput_ratio,
        )

    return ModelGatewayBenchmarkReport(
        schema_version=MODEL_GATEWAY_EVALUATION_SCHEMA_VERSION,
        platform_version=__version__,
        platform_commit=platform_commit,
        started_at=started_at,
        duration_seconds=round(time.perf_counter() - started, 6),
        benchmark=spec,
        targets=results,
        comparison_to_direct=deltas,
    )


async def _run_target(
    *,
    target_name: str,
    provider: ModelProvider,
    spec: ModelGatewayBenchmarkSpec,
) -> ModelGatewayTargetResult:
    for index in range(spec.warmup_operations):
        await _invoke_without_evidence(
            provider=provider,
            spec=spec,
            request_id=f"gateway-eval-{target_name}-warmup-{index}",
        )

    semaphore = asyncio.Semaphore(spec.concurrency)
    latencies: list[float] = []
    error_counts: Counter[str] = Counter()
    successful = 0
    identity_preserved = True

    async def invoke(index: int) -> None:
        nonlocal successful, identity_preserved
        async with semaphore:
            request_id = f"gateway-eval-{target_name}-{index}"
            request = _request(spec=spec, request_id=request_id, target_name=target_name)
            operation_started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    provider.generate(request),
                    timeout=spec.timeout_seconds,
                )
            except ContractError as exc:
                error_counts[exc.code.value] += 1
            except TimeoutError:
                error_counts["timeout"] += 1
            except Exception:  # noqa: BLE001 - benchmark must count without leaking raw errors.
                error_counts["unexpected_error"] += 1
            else:
                successful += 1
                if response.model_ref != spec.canonical_model_id:
                    identity_preserved = False
            finally:
                latencies.append(time.perf_counter() - operation_started)

    target_started = time.perf_counter()
    await asyncio.gather(*(invoke(index) for index in range(spec.operation_count)))
    duration = time.perf_counter() - target_started
    failed = spec.operation_count - successful
    throughput = successful / duration if duration > 0 else 0.0

    return ModelGatewayTargetResult(
        target_name=target_name,
        provider_id=provider.descriptor.provider_id,
        attempted_operations=spec.operation_count,
        successful_operations=successful,
        failed_operations=failed,
        duration_seconds=round(duration, 6),
        throughput_operations_per_second=round(throughput, 6),
        latency=LatencyDistribution.from_seconds(latencies),
        canonical_identity_preserved=identity_preserved,
        canonical_error_counts=dict(sorted(error_counts.items())),
    )


async def _invoke_without_evidence(
    *,
    provider: ModelProvider,
    spec: ModelGatewayBenchmarkSpec,
    request_id: str,
) -> None:
    request = _request(spec=spec, request_id=request_id, target_name="warmup")
    try:
        await asyncio.wait_for(provider.generate(request), timeout=spec.timeout_seconds)
    except Exception:  # noqa: BLE001 - warmups are deliberately excluded from evidence.
        return


def _request(
    *,
    spec: ModelGatewayBenchmarkSpec,
    request_id: str,
    target_name: str,
) -> ModelRequest:
    return ModelRequest(
        request_id=request_id,
        messages=(spec.prompt,),
        context=OperationContext(correlation_id=f"issue-859:{target_name}:{request_id}"),
        requirements={"model_config_id": spec.canonical_model_id},
    )
