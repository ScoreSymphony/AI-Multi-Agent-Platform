"""Versioned contracts for platform performance benchmark evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

BENCHMARK_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class BenchmarkSpec:
    """One reproducible benchmark workload definition."""

    benchmark_id: str
    benchmark_version: str
    deployment_profile: str
    operation_count: int
    concurrency: int
    warmup_operations: int = 0
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip():
            raise ValueError("benchmark_id must not be empty")
        if not self.benchmark_version.strip():
            raise ValueError("benchmark_version must not be empty")
        if self.operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if self.concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True, slots=True)
class LatencyDistribution:
    """A deliberately small latency summary without fake sub-millisecond precision."""

    count: int
    min_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    @classmethod
    def from_seconds(cls, samples: list[float]) -> LatencyDistribution:
        if not samples:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        values_ms = sorted(value * 1000.0 for value in samples)
        return cls(
            count=len(values_ms),
            min_ms=_round_ms(values_ms[0]),
            mean_ms=_round_ms(sum(values_ms) / len(values_ms)),
            p50_ms=_round_ms(_percentile(values_ms, 0.50)),
            p95_ms=_round_ms(_percentile(values_ms, 0.95)),
            p99_ms=_round_ms(_percentile(values_ms, 0.99)),
            max_ms=_round_ms(values_ms[-1]),
        )


@dataclass(frozen=True, slots=True)
class ResourceMetrics:
    """Process- and data-root measurements available without a paid monitoring service."""

    process_cpu_seconds: float
    traced_memory_current_bytes: int
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    storage_bytes_before: int
    storage_bytes_after: int
    storage_growth_bytes: int
    open_file_descriptors: int | None


@dataclass(frozen=True, slots=True)
class CorrectnessSummary:
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    duplicate_task_ids: int
    duplicate_run_ids: int
    timeline_failures: int
    passed: bool


@dataclass(frozen=True, slots=True)
class BaselineComparison:
    comparable: bool
    classification: str
    reasons: tuple[str, ...]
    p95_latency_change_ratio: float | None = None
    throughput_change_ratio: float | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Machine-readable evidence emitted by a benchmark run."""

    schema_version: str
    benchmark: BenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    duration_seconds: float
    environment: Mapping[str, Any]
    throughput_operations_per_second: float
    operation_latency: LatencyDistribution
    admission_latency: LatencyDistribution
    execution_latency: LatencyDistribution
    inspection_latency: LatencyDistribution
    resources: ResourceMetrics
    correctness: CorrectnessSummary
    errors: tuple[str, ...]
    baseline_comparison: BaselineComparison | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RegressionThresholds:
    """Optional evidence-backed budgets supplied by the caller.

    The benchmark framework intentionally does not invent universal default budgets.
    """

    max_p95_latency_regression_ratio: float | None = None
    max_throughput_regression_ratio: float | None = None

    def __post_init__(self) -> None:
        for value in (
            self.max_p95_latency_regression_ratio,
            self.max_throughput_regression_ratio,
        ):
            if value is not None and value < 0:
                raise ValueError("regression thresholds must not be negative")


def compare_with_baseline(
    report: BenchmarkReport,
    baseline: Mapping[str, Any],
    thresholds: RegressionThresholds,
) -> BaselineComparison:
    """Compare two environment-compatible reports using caller-provided budgets."""

    reasons: list[str] = []
    baseline_benchmark = _mapping(baseline.get("benchmark"))
    for key, expected in (
        ("benchmark_id", report.benchmark.benchmark_id),
        ("benchmark_version", report.benchmark.benchmark_version),
        ("deployment_profile", report.benchmark.deployment_profile),
        ("concurrency", report.benchmark.concurrency),
    ):
        if baseline_benchmark.get(key) != expected:
            reasons.append(f"baseline {key} is not comparable")

    baseline_environment = _mapping(baseline.get("environment"))
    for key in ("python_implementation", "python_major_minor", "system"):
        if baseline_environment.get(key) != report.environment.get(key):
            reasons.append(f"baseline environment differs for {key}")

    if reasons:
        return BaselineComparison(False, "incomparable", tuple(reasons))

    baseline_latency = _mapping(baseline.get("operation_latency"))
    baseline_p95 = _positive_float(baseline_latency.get("p95_ms"))
    baseline_throughput = _positive_float(
        baseline.get("throughput_operations_per_second")
    )
    if baseline_p95 is None or baseline_throughput is None:
        return BaselineComparison(
            False,
            "incomparable",
            ("baseline is missing positive p95 latency or throughput metrics",),
        )

    latency_change = report.operation_latency.p95_ms / baseline_p95 - 1.0
    throughput_change = report.throughput_operations_per_second / baseline_throughput - 1.0
    violations: list[str] = []
    if (
        thresholds.max_p95_latency_regression_ratio is not None
        and latency_change > thresholds.max_p95_latency_regression_ratio
    ):
        violations.append("p95 latency regression exceeds configured threshold")
    if (
        thresholds.max_throughput_regression_ratio is not None
        and throughput_change < -thresholds.max_throughput_regression_ratio
    ):
        violations.append("throughput regression exceeds configured threshold")

    if violations:
        classification = "regression"
    elif (
        thresholds.max_p95_latency_regression_ratio is None
        and thresholds.max_throughput_regression_ratio is None
    ):
        classification = "comparable_no_budget"
    else:
        classification = "pass"
    return BaselineComparison(
        True,
        classification,
        tuple(violations),
        p95_latency_change_ratio=round(latency_change, 6),
        throughput_change_ratio=round(throughput_change, 6),
    )


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(sorted_values) - 1)
    fraction = position - lower_index
    return (
        sorted_values[lower_index]
        + (sorted_values[upper_index] - sorted_values[lower_index]) * fraction
    )


def _round_ms(value: float) -> float:
    return round(value, 3)


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if converted > 0 else None
