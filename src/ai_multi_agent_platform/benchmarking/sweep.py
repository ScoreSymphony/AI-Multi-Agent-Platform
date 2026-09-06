"""Concurrency sweep orchestration for deterministic single-node benchmarks."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.deployment import SingleNodeConfig

from .models import BenchmarkReport, BenchmarkSpec
from .single_node import SingleNodeBenchmarkHarness

SWEEP_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One measured concurrency/repetition point in a scale sweep."""

    concurrency: int
    repetition: int
    report_file: str
    throughput_operations_per_second: float
    p95_latency_ms: float
    duration_seconds: float
    completed_operations: int
    storage_growth_bytes: int
    correctness_passed: bool


@dataclass(frozen=True, slots=True)
class SingleNodeSweepReport:
    """Machine-readable summary across independent fresh single-node runs."""

    schema_version: str
    benchmark_id: str
    benchmark_version: str
    platform_version: str
    platform_commit: str
    started_at: str
    deployment_profile: str
    persistence_profile: str
    workload_distribution: str
    operation_count_per_point: int
    warmup_operations: int
    timeout_seconds: float
    repetitions: int
    concurrency_levels: tuple[int, ...]
    environment: Mapping[str, Any]
    points: tuple[SweepPoint, ...]
    correctness_passed: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version,
            "platform_version": self.platform_version,
            "platform_commit": self.platform_commit,
            "started_at": self.started_at,
            "deployment_profile": self.deployment_profile,
            "persistence_profile": self.persistence_profile,
            "workload_distribution": self.workload_distribution,
            "operation_count_per_point": self.operation_count_per_point,
            "warmup_operations": self.warmup_operations,
            "timeout_seconds": self.timeout_seconds,
            "repetitions": self.repetitions,
            "concurrency_levels": list(self.concurrency_levels),
            "environment": dict(self.environment),
            "points": [asdict(point) for point in self.points],
            "correctness_passed": self.correctness_passed,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class SingleNodeSweepExecution:
    """Aggregate summary plus the full point reports that produced it."""

    summary: SingleNodeSweepReport
    point_reports: tuple[tuple[SweepPoint, BenchmarkReport], ...]


class SingleNodeSweepHarness:
    """Run each scale point in a separate fresh production-shaped data root."""

    def __init__(self, data_root: Path, *, platform_commit: str = "unknown") -> None:
        self._data_root = data_root
        self._platform_commit = platform_commit

    async def run(
        self,
        *,
        concurrency_levels: Sequence[int],
        operation_count: int,
        warmup_operations: int,
        timeout_seconds: float,
        repetitions: int = 1,
    ) -> SingleNodeSweepExecution:
        levels = _validate_levels(concurrency_levels)
        if operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        _require_fresh_root(self._data_root)
        self._data_root.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(UTC).isoformat()
        point_reports: list[tuple[SweepPoint, BenchmarkReport]] = []
        errors: list[str] = []
        reference_environment: Mapping[str, Any] | None = None

        for concurrency in levels:
            for repetition in range(1, repetitions + 1):
                relative_report = f"c-{concurrency}-r-{repetition}.json"
                point_root = self._data_root / f"c-{concurrency}-r-{repetition}"
                report = await SingleNodeBenchmarkHarness(
                    SingleNodeConfig(data_dir=point_root, secure_cookie=False),
                    platform_commit=self._platform_commit,
                ).run(
                    BenchmarkSpec(
                        benchmark_id="single-node.reference.lifecycle",
                        benchmark_version="1.0",
                        deployment_profile="single-node-reference",
                        operation_count=operation_count,
                        concurrency=concurrency,
                        warmup_operations=warmup_operations,
                        timeout_seconds=timeout_seconds,
                    )
                )
                if reference_environment is None:
                    reference_environment = report.environment
                elif dict(reference_environment) != dict(report.environment):
                    errors.append(
                        f"environment changed during sweep at concurrency={concurrency} "
                        f"repetition={repetition}"
                    )
                if not report.correctness.passed:
                    errors.append(
                        f"correctness failed at concurrency={concurrency} repetition={repetition}"
                    )

                point = SweepPoint(
                    concurrency=concurrency,
                    repetition=repetition,
                    report_file=relative_report,
                    throughput_operations_per_second=report.throughput_operations_per_second,
                    p95_latency_ms=report.operation_latency.p95_ms,
                    duration_seconds=report.duration_seconds,
                    completed_operations=report.correctness.completed_operations,
                    storage_growth_bytes=report.resources.storage_growth_bytes,
                    correctness_passed=report.correctness.passed,
                )
                point_reports.append((point, report))

        summary = SingleNodeSweepReport(
            schema_version=SWEEP_REPORT_SCHEMA_VERSION,
            benchmark_id="single-node.reference.lifecycle.sweep",
            benchmark_version="1.0",
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            workload_distribution="deterministic-task-lifecycle",
            operation_count_per_point=operation_count,
            warmup_operations=warmup_operations,
            timeout_seconds=timeout_seconds,
            repetitions=repetitions,
            concurrency_levels=levels,
            environment=reference_environment or {},
            points=tuple(point for point, _ in point_reports),
            correctness_passed=not errors,
            errors=tuple(errors),
        )
        return SingleNodeSweepExecution(summary=summary, point_reports=tuple(point_reports))


def _validate_levels(levels: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(levels)
    if not normalized:
        raise ValueError("at least one concurrency level is required")
    if any(level < 1 for level in normalized):
        raise ValueError("concurrency levels must be positive")
    if len(set(normalized)) != len(normalized):
        raise ValueError("concurrency levels must be unique")
    return normalized


def _require_fresh_root(root: Path) -> None:
    if not root.exists():
        return
    try:
        has_entries = next(root.iterdir(), None) is not None
    except OSError as exc:
        raise ValueError(f"sweep data root cannot be inspected: {root}") from exc
    if has_entries:
        raise ValueError("single-node sweep requires a fresh empty data root")
