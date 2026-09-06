"""Deterministic model/tool/provider degradation benchmarks for issue #440."""

from __future__ import annotations

import asyncio
import time
import tracemalloc
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationTrace,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OperationContext,
    OperationControl,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    ModelRuntime,
)

from .models import LatencyDistribution
from .single_node import _environment_metadata, _open_file_descriptor_count, _peak_rss_bytes

PROVIDER_FAULT_REPORT_SCHEMA_VERSION = "1.0"

_SCENARIOS = {
    "model-latency",
    "model-unavailable",
    "model-cancelled",
    "tool-unavailable",
    "tool-timeout",
    "tool-cancelled",
}

type _PhaseInvoker = Callable[[int, str], Awaitable[tuple[str, float]]]


@dataclass(frozen=True, slots=True)
class ProviderFaultBenchmarkSpec:
    """One bounded deterministic provider-degradation scenario."""

    benchmark_id: str
    benchmark_version: str
    scenario: str
    operations_per_phase: int
    concurrency: int
    fault_delay_seconds: float = 0.05
    tool_timeout_seconds: float = 0.01
    cancel_after_seconds: float = 0.005
    operation_timeout_seconds: float = 2.0
    safety_max_operations_per_phase: int = 1000
    safety_max_concurrency: int = 64
    expected_invariants: tuple[str, ...] = (
        "baseline-and-recovery-phases-succeed",
        "fault-phase-uses-canonical-provider-neutral-errors",
        "no-implicit-retry-loop-is-introduced",
        "provider-service-time-is-separated-from-platform-overhead",
        "recovery-restores-successful-invocation",
    )
    captured_metrics: tuple[str, ...] = (
        "phase-end-to-end-latency-p50-p95-p99",
        "provider-service-latency-p50-p95-p99",
        "derived-platform-overhead-p50-p95-p99",
        "phase-throughput",
        "canonical-error-and-retryable-counts",
        "provider-cancellation-counts",
        "cpu-memory-open-files",
        "correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.scenario not in _SCENARIOS:
            raise ValueError("unsupported provider fault scenario")
        if self.operations_per_phase < 1:
            raise ValueError("operations_per_phase must be positive")
        if self.concurrency < 1:
            raise ValueError("concurrency must be positive")
        if self.safety_max_operations_per_phase < 1:
            raise ValueError("safety_max_operations_per_phase must be positive")
        if self.safety_max_concurrency < 1:
            raise ValueError("safety_max_concurrency must be positive")
        if self.operations_per_phase > self.safety_max_operations_per_phase:
            raise ValueError("operations_per_phase exceeds configured safety limit")
        if self.concurrency > self.safety_max_concurrency:
            raise ValueError("concurrency exceeds configured safety limit")
        if self.fault_delay_seconds <= 0:
            raise ValueError("fault_delay_seconds must be positive")
        if self.tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be positive")
        if self.cancel_after_seconds <= 0:
            raise ValueError("cancel_after_seconds must be positive")
        if self.operation_timeout_seconds <= 0:
            raise ValueError("operation_timeout_seconds must be positive")
        if (
            self.scenario == "tool-timeout"
            and self.fault_delay_seconds <= self.tool_timeout_seconds
        ):
            raise ValueError("tool-timeout requires fault_delay_seconds > tool_timeout_seconds")
        if self.scenario.endswith("cancelled") and (
            self.fault_delay_seconds <= self.cancel_after_seconds
        ):
            raise ValueError(
                "cancelled scenarios require fault_delay_seconds > cancel_after_seconds"
            )


@dataclass(frozen=True, slots=True)
class ProviderFaultResourceMetrics:
    process_cpu_seconds: float
    traced_memory_current_bytes: int
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    open_file_descriptors: int | None


@dataclass(frozen=True, slots=True)
class ProviderFaultCorrectnessSummary:
    attempted_operations: int
    successful_operations: int
    expected_failures: int
    unexpected_failures: int
    retryable_failures: int
    baseline_successes: int
    fault_successes: int
    recovery_successes: int
    provider_cancelled_calls: int
    automatic_retry_attempts: int
    recovered: bool
    passed: bool


@dataclass(frozen=True, slots=True)
class ProviderFaultBenchmarkReport:
    schema_version: str
    benchmark: ProviderFaultBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    expected_fault_error_code: str | None
    phase_throughput_operations_per_second: Mapping[str, float]
    phase_latency: Mapping[str, LatencyDistribution]
    provider_service_latency: Mapping[str, LatencyDistribution]
    platform_overhead_latency: Mapping[str, LatencyDistribution]
    resources: ProviderFaultResourceMetrics
    correctness: ProviderFaultCorrectnessSummary
    error_counts: Mapping[str, int]
    retryable_error_counts: Mapping[str, int]
    measurements: Mapping[str, int | float | str | bool | None]
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phase_throughput_operations_per_second"] = dict(
            self.phase_throughput_operations_per_second
        )
        payload["error_counts"] = dict(self.error_counts)
        payload["retryable_error_counts"] = dict(self.retryable_error_counts)
        payload["measurements"] = dict(self.measurements)
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in ("expected_invariants", "captured_metrics"):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(slots=True)
class _PhaseEvidence:
    latencies: list[float] = field(default_factory=list)
    provider_latencies: list[float] = field(default_factory=list)
    overhead_latencies: list[float] = field(default_factory=list)
    successes: int = 0
    expected_failures: int = 0
    unexpected_failures: int = 0
    error_counts: Counter[str] = field(default_factory=Counter)
    retryable_error_counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


class _BenchmarkModelProvider(ModelProvider):
    def __init__(self) -> None:
        self.mode = "healthy"
        self.delay_seconds = 0.0
        self.service_seconds: dict[str, float] = {}
        self.cancelled_calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        health = HealthStatus.HEALTHY if self.mode == "healthy" else HealthStatus.DEGRADED
        return ProviderDescriptor(
            provider_id="benchmark-model-provider",
            provider_type="deterministic-benchmark",
            supported_operations=("generate",),
            capabilities=(
                Capability(
                    name="model.generate",
                    kind=CapabilityKind.MODEL,
                    supported_operations=("generate",),
                ),
            ),
            health=health,
            available=True,
        )

    async def health(self) -> HealthStatus:
        return self.descriptor.health

    async def generate(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        try:
            if self.mode in {"delayed", "cancelled"}:
                await asyncio.sleep(self.delay_seconds)
            if self.mode == "unavailable":
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "deterministic benchmark model provider unavailable",
                    provider_id=self.descriptor.provider_id,
                    retryable=True,
                )
            return ModelResponse(
                request_id=request.request_id,
                text="benchmark-response",
                model_ref="fixture-native-model",
                usage={"input_tokens": 1, "output_tokens": 1},
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="benchmark-provider",
                        values={"mode": self.mode},
                    ),
                ),
            )
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise
        finally:
            self.service_seconds[request.request_id] = time.perf_counter() - started


class _BenchmarkToolProvider(CapabilityToolProvider):
    def __init__(self, *, timeout_seconds: float) -> None:
        self.mode = "healthy"
        self.delay_seconds = 0.0
        self.service_seconds: dict[str, float] = {}
        self.cancelled_calls = 0
        self._timeout_seconds = timeout_seconds

    @property
    def descriptor(self) -> ProviderDescriptor:
        health = HealthStatus.HEALTHY if self.mode == "healthy" else HealthStatus.DEGRADED
        return ProviderDescriptor(
            provider_id="benchmark-tool-provider",
            provider_type="deterministic-benchmark",
            supported_operations=("invoke",),
            capabilities=(
                Capability(
                    name="tool.benchmark",
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=health,
            available=True,
        )

    async def health(self) -> HealthStatus:
        return self.descriptor.health

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id="tool.benchmark",
                    name="Benchmark deterministic tool",
                    version="1.0",
                    input_schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    output_schema={
                        "type": "object",
                        "properties": {"message": {"type": "string"}},
                        "required": ["message"],
                        "additionalProperties": False,
                    },
                    timeout_seconds=self._timeout_seconds,
                    health=HealthStatus.HEALTHY,
                    available=True,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="benchmark.echo",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        started = time.perf_counter()
        try:
            if self.mode in {"delayed", "cancelled"}:
                await asyncio.sleep(self.delay_seconds)
            if self.mode == "unavailable":
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "deterministic benchmark tool provider unavailable",
                    provider_id=self.descriptor.provider_id,
                    retryable=True,
                )
            arguments = invocation.arguments_json()
            return ToolResult(
                invocation_id=invocation.invocation_id,
                output={"message": str(arguments.get("message", ""))},
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="benchmark-provider",
                        values={"mode": self.mode},
                    ),
                ),
            )
        except asyncio.CancelledError:
            self.cancelled_calls += 1
            raise
        finally:
            self.service_seconds[invocation.invocation_id] = time.perf_counter() - started


class ProviderFaultBenchmarkHarness:
    """Measure canonical model/tool degradation under bounded concurrent load."""

    def __init__(self, *, platform_commit: str = "unknown") -> None:
        self._platform_commit = platform_commit

    async def run(self, spec: ProviderFaultBenchmarkSpec) -> ProviderFaultBenchmarkReport:
        tracing_was_active = tracemalloc.is_tracing()
        if not tracing_was_active:
            tracemalloc.start()
        cpu_before = time.process_time()
        started_at = datetime.now(UTC).isoformat()
        measurement_started = time.perf_counter()

        errors: list[str] = []
        if spec.scenario.startswith("model-"):
            phases, cancelled_calls = await self._run_model_scenario(spec)
        else:
            phases, cancelled_calls = await self._run_tool_scenario(spec)

        duration = time.perf_counter() - measurement_started
        process_cpu_seconds = time.process_time() - cpu_before
        traced_current, traced_peak = tracemalloc.get_traced_memory()
        if not tracing_was_active:
            tracemalloc.stop()

        error_counts: Counter[str] = Counter()
        retryable_counts: Counter[str] = Counter()
        for evidence in phases.values():
            error_counts.update(evidence.error_counts)
            retryable_counts.update(evidence.retryable_error_counts)
            errors.extend(evidence.errors)

        expected_code = _expected_fault_error_code(spec.scenario)
        expected_retryable = _expected_fault_retryable(spec.scenario)
        baseline = phases["baseline"]
        fault = phases["fault"]
        recovery = phases["recovery"]
        expected_faults = 0 if expected_code is None else spec.operations_per_phase
        expected_fault_successes = spec.operations_per_phase if expected_code is None else 0
        recovered = recovery.successes == spec.operations_per_phase
        passed = (
            baseline.successes == spec.operations_per_phase
            and fault.successes == expected_fault_successes
            and fault.expected_failures == expected_faults
            and sum(item.unexpected_failures for item in phases.values()) == 0
            and recovered
            and not errors
        )
        if expected_code is not None:
            expected_retryable_counts = (
                Counter({expected_code.value: expected_faults})
                if expected_retryable
                else Counter()
            )
            passed = passed and error_counts == Counter({expected_code.value: expected_faults})
            passed = passed and retryable_counts == expected_retryable_counts
        else:
            passed = passed and not error_counts and not retryable_counts
        if spec.scenario in {"model-cancelled", "tool-cancelled", "tool-timeout"}:
            passed = passed and cancelled_calls == spec.operations_per_phase

        total_successes = sum(item.successes for item in phases.values())
        total_expected_failures = sum(item.expected_failures for item in phases.values())
        total_unexpected_failures = sum(item.unexpected_failures for item in phases.values())
        return ProviderFaultBenchmarkReport(
            schema_version=PROVIDER_FAULT_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            duration_seconds=round(duration, 6),
            environment=_environment_metadata(),
            expected_fault_error_code=expected_code.value if expected_code is not None else None,
            phase_throughput_operations_per_second={
                name: round(spec.operations_per_phase / evidence.duration_seconds, 6)
                if evidence.duration_seconds > 0
                else 0.0
                for name, evidence in phases.items()
            },
            phase_latency={
                name: LatencyDistribution.from_seconds(evidence.latencies)
                for name, evidence in phases.items()
            },
            provider_service_latency={
                name: LatencyDistribution.from_seconds(evidence.provider_latencies)
                for name, evidence in phases.items()
            },
            platform_overhead_latency={
                name: LatencyDistribution.from_seconds(evidence.overhead_latencies)
                for name, evidence in phases.items()
            },
            resources=ProviderFaultResourceMetrics(
                process_cpu_seconds=round(process_cpu_seconds, 6),
                traced_memory_current_bytes=traced_current,
                traced_memory_peak_bytes=traced_peak,
                peak_rss_bytes=_peak_rss_bytes(),
                open_file_descriptors=_open_file_descriptor_count(),
            ),
            correctness=ProviderFaultCorrectnessSummary(
                attempted_operations=spec.operations_per_phase * 3,
                successful_operations=total_successes,
                expected_failures=total_expected_failures,
                unexpected_failures=total_unexpected_failures,
                retryable_failures=sum(retryable_counts.values()),
                baseline_successes=baseline.successes,
                fault_successes=fault.successes,
                recovery_successes=recovery.successes,
                provider_cancelled_calls=cancelled_calls,
                automatic_retry_attempts=0,
                recovered=recovered,
                passed=passed,
            ),
            error_counts=dict(sorted(error_counts.items())),
            retryable_error_counts=dict(sorted(retryable_counts.items())),
            measurements={
                "fault_delay_seconds": spec.fault_delay_seconds,
                "tool_timeout_seconds": spec.tool_timeout_seconds,
                "cancel_after_seconds": spec.cancel_after_seconds,
                "operation_timeout_seconds": spec.operation_timeout_seconds,
                "provider_fault_fixture": "deterministic-local",
                "automatic_retry_policy": "none",
            },
            errors=tuple(errors),
        )

    async def _run_model_scenario(
        self,
        spec: ProviderFaultBenchmarkSpec,
    ) -> tuple[dict[str, _PhaseEvidence], int]:
        provider = _BenchmarkModelProvider()
        registry = ModelRegistry()
        registry.register_provider(provider)
        registry.register_model(
            ModelConfiguration(
                config_id="benchmark-model",
                display_name="Benchmark model",
                provider_id=provider.descriptor.provider_id,
                capabilities=ModelCapabilities(context_window=4096),
                location=ModelLocation.LOCAL,
                health=HealthStatus.HEALTHY,
                priority=100,
            )
        )
        runtime = ModelRuntime(registry)

        async def invoke(index: int, phase: str) -> tuple[str, float]:
            request_id = f"benchmark-model-{phase}-{index}"
            request = ModelRequest(
                request_id=request_id,
                messages=("benchmark",),
                context=OperationContext(
                    correlation_id=f"benchmark-model-{phase}-{index}",
                    control=OperationControl(),
                ),
                requirements={"model_config_id": "benchmark-model"},
            )
            started = time.perf_counter()
            if spec.scenario == "model-cancelled" and phase == "fault":
                task = asyncio.create_task(runtime.generate(request))
                await asyncio.sleep(spec.cancel_after_seconds)
                task.cancel()
                await task
            else:
                await asyncio.wait_for(
                    runtime.generate(request),
                    timeout=spec.operation_timeout_seconds,
                )
            return request_id, time.perf_counter() - started

        phases: dict[str, _PhaseEvidence] = {}
        provider.mode = "healthy"
        provider.delay_seconds = 0.0
        phases["baseline"] = await self._run_phase(
            spec,
            "baseline",
            invoke,
            provider.service_seconds,
            expected_code=None,
        )

        provider.mode = _model_fault_mode(spec.scenario)
        provider.delay_seconds = spec.fault_delay_seconds
        phases["fault"] = await self._run_phase(
            spec,
            "fault",
            invoke,
            provider.service_seconds,
            expected_code=_expected_fault_error_code(spec.scenario),
        )

        provider.mode = "healthy"
        provider.delay_seconds = 0.0
        phases["recovery"] = await self._run_phase(
            spec,
            "recovery",
            invoke,
            provider.service_seconds,
            expected_code=None,
        )
        return phases, provider.cancelled_calls

    async def _run_tool_scenario(
        self,
        spec: ProviderFaultBenchmarkSpec,
    ) -> tuple[dict[str, _PhaseEvidence], int]:
        capability_timeout = (
            spec.tool_timeout_seconds
            if spec.scenario == "tool-timeout"
            else spec.operation_timeout_seconds
        )
        provider = _BenchmarkToolProvider(timeout_seconds=capability_timeout)
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        invoker = CapabilityInvoker(registry)

        async def invoke(index: int, phase: str) -> tuple[str, float]:
            invocation_id = f"benchmark-tool-{phase}-{index}"
            correlation_id = f"benchmark-tool-{phase}-{index}"
            context = OperationContext(
                correlation_id=correlation_id,
                control=OperationControl(),
            )
            request = CapabilityInvocation(
                invocation_id=invocation_id,
                capability_id="tool.benchmark",
                arguments={"message": "benchmark"},
                context=context,
                trace=InvocationTrace(
                    correlation_id=correlation_id,
                    task_id=new_id("task"),
                    run_id=new_id("run"),
                    agent_id=new_id("agent"),
                ),
            )
            started = time.perf_counter()
            if spec.scenario == "tool-cancelled" and phase == "fault":
                task = asyncio.create_task(invoker.invoke(request))
                await asyncio.sleep(spec.cancel_after_seconds)
                task.cancel()
                await task
            else:
                await asyncio.wait_for(
                    invoker.invoke(request),
                    timeout=spec.operation_timeout_seconds,
                )
            return invocation_id, time.perf_counter() - started

        phases: dict[str, _PhaseEvidence] = {}
        provider.mode = "healthy"
        provider.delay_seconds = 0.0
        phases["baseline"] = await self._run_phase(
            spec,
            "baseline",
            invoke,
            provider.service_seconds,
            expected_code=None,
        )

        provider.mode = _tool_fault_mode(spec.scenario)
        provider.delay_seconds = spec.fault_delay_seconds
        phases["fault"] = await self._run_phase(
            spec,
            "fault",
            invoke,
            provider.service_seconds,
            expected_code=_expected_fault_error_code(spec.scenario),
        )

        provider.mode = "healthy"
        provider.delay_seconds = 0.0
        phases["recovery"] = await self._run_phase(
            spec,
            "recovery",
            invoke,
            provider.service_seconds,
            expected_code=None,
        )
        return phases, provider.cancelled_calls

    async def _run_phase(
        self,
        spec: ProviderFaultBenchmarkSpec,
        phase: str,
        invoke: _PhaseInvoker,
        provider_service_seconds: Mapping[str, float],
        *,
        expected_code: ErrorCode | None,
    ) -> _PhaseEvidence:
        evidence = _PhaseEvidence()
        semaphore = asyncio.Semaphore(spec.concurrency)
        phase_started = time.perf_counter()
        expected_retryable = _expected_fault_retryable(spec.scenario)

        async def run_one(index: int) -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    operation_id, latency = await invoke(index, phase)
                except ContractError as exc:
                    latency = time.perf_counter() - started
                    operation_id = _operation_id(spec.scenario, phase, index)
                    evidence.latencies.append(latency)
                    provider_latency = provider_service_seconds.get(operation_id, 0.0)
                    evidence.provider_latencies.append(provider_latency)
                    evidence.overhead_latencies.append(max(0.0, latency - provider_latency))
                    evidence.error_counts[exc.code.value] += 1
                    if exc.retryable:
                        evidence.retryable_error_counts[exc.code.value] += 1
                    if (
                        expected_code is not None
                        and exc.code is expected_code
                        and exc.retryable is expected_retryable
                    ):
                        evidence.expected_failures += 1
                    else:
                        evidence.unexpected_failures += 1
                        evidence.errors.append(
                            f"{phase} operation {index} returned unexpected "
                            f"{exc.code.value} retryable={exc.retryable}"
                        )
                    return
                except Exception as exc:
                    latency = time.perf_counter() - started
                    evidence.latencies.append(latency)
                    evidence.unexpected_failures += 1
                    evidence.errors.append(
                        f"{phase} operation {index} failed unexpectedly: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return

                evidence.latencies.append(latency)
                provider_latency = provider_service_seconds.get(operation_id, 0.0)
                evidence.provider_latencies.append(provider_latency)
                evidence.overhead_latencies.append(max(0.0, latency - provider_latency))
                if expected_code is None:
                    evidence.successes += 1
                else:
                    evidence.unexpected_failures += 1
                    evidence.errors.append(
                        f"{phase} operation {index} succeeded while "
                        f"{expected_code.value} was expected"
                    )

        await asyncio.gather(*(run_one(index) for index in range(spec.operations_per_phase)))
        evidence.duration_seconds = time.perf_counter() - phase_started
        return evidence


def _operation_id(scenario: str, phase: str, index: int) -> str:
    prefix = "benchmark-model" if scenario.startswith("model-") else "benchmark-tool"
    return f"{prefix}-{phase}-{index}"


def _expected_fault_error_code(scenario: str) -> ErrorCode | None:
    if scenario == "model-latency":
        return None
    if scenario in {"model-unavailable", "tool-unavailable"}:
        return ErrorCode.UNAVAILABLE
    if scenario == "tool-timeout":
        return ErrorCode.TIMEOUT
    if scenario in {"model-cancelled", "tool-cancelled"}:
        return ErrorCode.CANCELLED
    raise ValueError(f"unsupported provider fault scenario: {scenario}")


def _expected_fault_retryable(scenario: str) -> bool | None:
    if scenario == "model-latency":
        return None
    if scenario == "model-cancelled":
        return False
    if scenario in {
        "model-unavailable",
        "tool-unavailable",
        "tool-timeout",
        "tool-cancelled",
    }:
        return True
    raise ValueError(f"unsupported provider fault scenario: {scenario}")


def _model_fault_mode(scenario: str) -> str:
    if scenario == "model-latency":
        return "delayed"
    if scenario == "model-unavailable":
        return "unavailable"
    if scenario == "model-cancelled":
        return "cancelled"
    raise ValueError(f"unsupported model fault scenario: {scenario}")


def _tool_fault_mode(scenario: str) -> str:
    if scenario == "tool-unavailable":
        return "unavailable"
    if scenario == "tool-timeout":
        return "delayed"
    if scenario == "tool-cancelled":
        return "cancelled"
    raise ValueError(f"unsupported tool fault scenario: {scenario}")