"""Controlled single-node saturation stress benchmarks for issue #440."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.deployment import SingleNodeConfig

from .models import BenchmarkReport, BenchmarkSpec
from .single_node import SingleNodeBenchmarkHarness, _environment_metadata

STRESS_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class StressBenchmarkSpec:
    """Versioned, explicitly bounded concurrency-saturation specification."""

    benchmark_id: str
    benchmark_version: str
    deployment_profile: str
    persistence_profile: str
    concurrency_levels: tuple[int, ...]
    operations_per_level: int
    warmup_operations: int
    timeout_seconds: float
    safety_max_concurrency: int
    safety_max_operations_per_level: int
    stop_on_correctness_failure: bool = True
    optional_subsystems: tuple[str, ...] = ()
    expected_invariants: tuple[str, ...] = (
        "fresh-state-per-stress-point",
        "authorized-canonical-lifecycle",
        "stop-before-host-safety-bound",
        "correctness-failure-stops-escalation",
    )
    captured_metrics: tuple[str, ...] = (
        "throughput-by-concurrency",
        "latency-p50-p95-p99-by-concurrency",
        "error-rate-by-concurrency",
        "cpu-memory-storage-by-concurrency",
        "correctness",
    )

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.benchmark_version.strip():
            raise ValueError("benchmark_id and benchmark_version must not be empty")
        if self.deployment_profile != "single-node-reference":
            raise ValueError("stress benchmark requires single-node-reference deployment")
        if not self.concurrency_levels:
            raise ValueError("stress benchmark requires at least one concurrency level")
        if any(level < 1 for level in self.concurrency_levels):
            raise ValueError("stress concurrency levels must be positive")
        if tuple(sorted(set(self.concurrency_levels))) != self.concurrency_levels:
            raise ValueError("stress concurrency levels must be unique and strictly increasing")
        if self.operations_per_level < 1:
            raise ValueError("operations_per_level must be at least 1")
        if self.warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.safety_max_concurrency < 1:
            raise ValueError("safety_max_concurrency must be positive")
        if self.safety_max_operations_per_level < 1:
            raise ValueError("safety_max_operations_per_level must be positive")
        if self.concurrency_levels[-1] > self.safety_max_concurrency:
            raise ValueError("requested stress concurrency exceeds explicit safety limit")
        if self.operations_per_level > self.safety_max_operations_per_level:
            raise ValueError("requested stress operations exceed explicit safety limit")


@dataclass(frozen=True, slots=True)
class StressPoint:
    concurrency: int
    report_file: str
    duration_seconds: float
    throughput_operations_per_second: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    attempted_operations: int
    completed_operations: int
    failed_operations: int
    error_rate: float
    traced_memory_peak_bytes: int
    peak_rss_bytes: int | None
    storage_growth_bytes: int
    correctness_passed: bool


@dataclass(frozen=True, slots=True)
class StressBenchmarkReport:
    schema_version: str
    benchmark: StressBenchmarkSpec
    platform_version: str
    platform_commit: str
    started_at: str
    environment: Mapping[str, Any]
    points: tuple[StressPoint, ...]
    requested_levels: tuple[int, ...]
    completed_levels: tuple[int, ...]
    stop_reason: str
    first_failed_concurrency: int | None
    correctness_passed: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["requested_levels"] = list(self.requested_levels)
        payload["completed_levels"] = list(self.completed_levels)
        payload["points"] = [asdict(point) for point in self.points]
        payload["errors"] = list(self.errors)
        benchmark = payload["benchmark"]
        if isinstance(benchmark, dict):
            for key in (
                "concurrency_levels",
                "optional_subsystems",
                "expected_invariants",
                "captured_metrics",
            ):
                benchmark[key] = list(benchmark[key])
        return payload


@dataclass(frozen=True, slots=True)
class SingleNodeStressExecution:
    summary: StressBenchmarkReport
    point_reports: tuple[tuple[StressPoint, BenchmarkReport], ...]


class SingleNodeStressHarness:
    """Escalate canonical lifecycle concurrency only within explicit host-safety bounds."""

    def __init__(self, data_root: Path, *, platform_commit: str = "unknown") -> None:
        self._data_root = data_root
        self._platform_commit = platform_commit

    async def run(self, spec: StressBenchmarkSpec) -> SingleNodeStressExecution:
        _require_fresh_root(self._data_root)
        self._data_root.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(UTC).isoformat()
        points: list[tuple[StressPoint, BenchmarkReport]] = []
        errors: list[str] = []
        reference_environment: Mapping[str, Any] | None = None
        first_failed_concurrency: int | None = None
        stop_reason = "completed-levels"

        for concurrency in spec.concurrency_levels:
            point_root = self._data_root / f"c-{concurrency}"
            report = await SingleNodeBenchmarkHarness(
                SingleNodeConfig(data_dir=point_root, secure_cookie=False),
                platform_commit=self._platform_commit,
            ).run(
                BenchmarkSpec(
                    benchmark_id="single-node.reference.lifecycle.stress-point",
                    benchmark_version=spec.benchmark_version,
                    deployment_profile=spec.deployment_profile,
                    operation_count=spec.operations_per_level,
                    concurrency=concurrency,
                    warmup_operations=spec.warmup_operations,
                    timeout_seconds=spec.timeout_seconds,
                )
            )
            if reference_environment is None:
                reference_environment = report.environment
            elif dict(reference_environment) != dict(report.environment):
                errors.append(f"environment changed during stress run at concurrency={concurrency}")

            attempted = report.correctness.attempted_operations
            failed = report.correctness.failed_operations
            error_rate = failed / attempted if attempted else 0.0
            point = StressPoint(
                concurrency=concurrency,
                report_file=f"c-{concurrency}.json",
                duration_seconds=report.duration_seconds,
                throughput_operations_per_second=report.throughput_operations_per_second,
                p50_latency_ms=report.operation_latency.p50_ms,
                p95_latency_ms=report.operation_latency.p95_ms,
                p99_latency_ms=report.operation_latency.p99_ms,
                attempted_operations=attempted,
                completed_operations=report.correctness.completed_operations,
                failed_operations=failed,
                error_rate=round(error_rate, 6),
                traced_memory_peak_bytes=report.resources.traced_memory_peak_bytes,
                peak_rss_bytes=report.resources.peak_rss_bytes,
                storage_growth_bytes=report.resources.storage_growth_bytes,
                correctness_passed=report.correctness.passed,
            )
            points.append((point, report))

            if not report.correctness.passed:
                errors.append(f"canonical correctness failed at concurrency={concurrency}")
                first_failed_concurrency = concurrency
                if spec.stop_on_correctness_failure:
                    stop_reason = "correctness-failure"
                    break

        completed_levels = tuple(point.concurrency for point, _ in points)
        correctness_passed = not errors and completed_levels == spec.concurrency_levels
        summary = StressBenchmarkReport(
            schema_version=STRESS_REPORT_SCHEMA_VERSION,
            benchmark=spec,
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            environment=reference_environment or _environment_metadata(),
            points=tuple(point for point, _ in points),
            requested_levels=spec.concurrency_levels,
            completed_levels=completed_levels,
            stop_reason=stop_reason,
            first_failed_concurrency=first_failed_concurrency,
            correctness_passed=correctness_passed,
            errors=tuple(errors),
        )
        return SingleNodeStressExecution(summary=summary, point_reports=tuple(points))


def _require_fresh_root(root: Path) -> None:
    if not root.exists():
        return
    try:
        has_entries = next(root.iterdir(), None) is not None
    except OSError as exc:
        raise ValueError(f"stress data root cannot be inspected: {root}") from exc
    if has_entries:
        raise ValueError("single-node stress requires a fresh empty data root")
