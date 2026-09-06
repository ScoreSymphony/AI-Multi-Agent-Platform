"""Growing-state persistence scalability sweeps for issue #440."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.deployment import SingleNodeConfig

from .workloads import (
    SingleNodeWorkloadHarness,
    WorkloadBenchmarkReport,
    WorkloadBenchmarkSpec,
)

PERSISTENCE_SWEEP_REPORT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class PersistenceScalePoint:
    """One independently seeded durable-state point in a persistence sweep."""

    seed_tasks: int
    repetition: int
    report_file: str
    throughput_operations_per_second: float
    read_p50_latency_ms: float
    read_p95_latency_ms: float
    read_p99_latency_ms: float
    restart_p50_latency_ms: float
    duration_seconds: float
    storage_bytes_after: int
    storage_bytes_per_seeded_task: float
    observed_tasks: int
    observed_runs: int
    correctness_passed: bool


@dataclass(frozen=True, slots=True)
class PersistenceScaleReport:
    """Machine-readable summary of query/restart behavior as durable state grows."""

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
    concurrency: int
    warmup_operations: int
    timeout_seconds: float
    repetitions: int
    seed_task_levels: tuple[int, ...]
    environment: Mapping[str, Any]
    points: tuple[PersistenceScalePoint, ...]
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
            "concurrency": self.concurrency,
            "warmup_operations": self.warmup_operations,
            "timeout_seconds": self.timeout_seconds,
            "repetitions": self.repetitions,
            "seed_task_levels": list(self.seed_task_levels),
            "environment": dict(self.environment),
            "points": [asdict(point) for point in self.points],
            "correctness_passed": self.correctness_passed,
            "errors": list(self.errors),
        }


@dataclass(frozen=True, slots=True)
class PersistenceScaleExecution:
    """Aggregate growing-state summary plus the complete evidence for every point."""

    summary: PersistenceScaleReport
    point_reports: tuple[tuple[PersistenceScalePoint, WorkloadBenchmarkReport], ...]


class SingleNodePersistenceScaleHarness:
    """Measure canonical query and restart behavior against increasing durable state."""

    def __init__(self, data_root: Path, *, platform_commit: str = "unknown") -> None:
        self._data_root = data_root
        self._platform_commit = platform_commit

    async def run(
        self,
        *,
        seed_task_levels: Sequence[int],
        operation_count: int,
        concurrency: int,
        warmup_operations: int,
        timeout_seconds: float,
        repetitions: int = 1,
    ) -> PersistenceScaleExecution:
        levels = _validate_seed_task_levels(seed_task_levels)
        if operation_count < 1:
            raise ValueError("operation_count must be at least 1")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if warmup_operations < 0:
            raise ValueError("warmup_operations must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        _require_fresh_root(self._data_root)
        self._data_root.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(UTC).isoformat()
        point_reports: list[tuple[PersistenceScalePoint, WorkloadBenchmarkReport]] = []
        errors: list[str] = []
        reference_environment: Mapping[str, Any] | None = None

        for seed_tasks in levels:
            for repetition in range(1, repetitions + 1):
                relative_report = f"state-{seed_tasks}-r-{repetition}.json"
                point_root = self._data_root / f"state-{seed_tasks}-r-{repetition}"
                report = await SingleNodeWorkloadHarness(
                    SingleNodeConfig(data_dir=point_root, secure_cookie=False),
                    platform_commit=self._platform_commit,
                ).run(
                    WorkloadBenchmarkSpec(
                        benchmark_id="single-node.persistence.growing-state.point",
                        benchmark_version="1.0",
                        scenario="restart",
                        deployment_profile="single-node-reference",
                        persistence_profile="sqlite-reference",
                        workload_distribution="restart-then-query-growing-state",
                        operation_count=operation_count,
                        concurrency=concurrency,
                        seed_tasks=seed_tasks,
                        warmup_operations=warmup_operations,
                        timeout_seconds=timeout_seconds,
                    )
                )

                if reference_environment is None:
                    reference_environment = report.environment
                elif dict(reference_environment) != dict(report.environment):
                    errors.append(
                        "environment changed during persistence sweep at "
                        f"seed_tasks={seed_tasks} repetition={repetition}"
                    )
                if not report.correctness.passed:
                    errors.append(
                        "correctness failed during persistence sweep at "
                        f"seed_tasks={seed_tasks} repetition={repetition}"
                    )

                storage_after = report.resources.storage_bytes_after
                point = PersistenceScalePoint(
                    seed_tasks=seed_tasks,
                    repetition=repetition,
                    report_file=relative_report,
                    throughput_operations_per_second=report.throughput_operations_per_second,
                    read_p50_latency_ms=report.read_latency.p50_ms,
                    read_p95_latency_ms=report.read_latency.p95_ms,
                    read_p99_latency_ms=report.read_latency.p99_ms,
                    restart_p50_latency_ms=report.restart_latency.p50_ms,
                    duration_seconds=report.duration_seconds,
                    storage_bytes_after=storage_after,
                    storage_bytes_per_seeded_task=round(storage_after / seed_tasks, 6),
                    observed_tasks=report.correctness.observed_tasks,
                    observed_runs=report.correctness.observed_runs,
                    correctness_passed=report.correctness.passed,
                )
                point_reports.append((point, report))

        summary = PersistenceScaleReport(
            schema_version=PERSISTENCE_SWEEP_REPORT_SCHEMA_VERSION,
            benchmark_id="single-node.persistence.growing-state.sweep",
            benchmark_version="1.0",
            platform_version=__version__,
            platform_commit=self._platform_commit,
            started_at=started_at,
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            workload_distribution="restart-then-query-growing-state",
            operation_count_per_point=operation_count,
            concurrency=concurrency,
            warmup_operations=warmup_operations,
            timeout_seconds=timeout_seconds,
            repetitions=repetitions,
            seed_task_levels=levels,
            environment=reference_environment or {},
            points=tuple(point for point, _ in point_reports),
            correctness_passed=not errors,
            errors=tuple(errors),
        )
        return PersistenceScaleExecution(
            summary=summary,
            point_reports=tuple(point_reports),
        )


def _validate_seed_task_levels(levels: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(levels)
    if not normalized:
        raise ValueError("at least one seed-task level is required")
    if any(level < 1 for level in normalized):
        raise ValueError("seed-task levels must be positive")
    if len(set(normalized)) != len(normalized):
        raise ValueError("seed-task levels must be unique")
    if tuple(sorted(normalized)) != normalized:
        raise ValueError("seed-task levels must be strictly increasing")
    return normalized


def _parse_seed_task_levels(value: str) -> tuple[int, ...]:
    fields = tuple(field.strip() for field in value.split(",") if field.strip())
    if not fields:
        raise ValueError("at least one seed-task level is required")
    try:
        levels = tuple(int(field) for field in fields)
    except ValueError as exc:
        raise ValueError("seed-task levels must be comma-separated integers") from exc
    return _validate_seed_task_levels(levels)


def _require_fresh_root(root: Path) -> None:
    if not root.exists():
        return
    try:
        has_entries = next(root.iterdir(), None) is not None
    except OSError as exc:
        raise ValueError(f"persistence sweep data root cannot be inspected: {root}") from exc
    if has_entries:
        raise ValueError("persistence sweep requires a fresh empty data root")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ai_multi_agent_platform.benchmarking.persistence",
        description=(
            "Measure canonical query, storage and restart behavior across increasing "
            "single-node durable-state sizes."
        ),
    )
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--seed-task-levels", default="10,100,1000")
    parser.add_argument("--operations-per-level", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--warmup-operations", type=int, default=5)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-persistence-sweep-")
        data_root = Path(temporary.name) / "data"
    else:
        data_root = args.data_root

    try:
        execution = await SingleNodePersistenceScaleHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(
            seed_task_levels=_parse_seed_task_levels(args.seed_task_levels),
            operation_count=args.operations_per_level,
            concurrency=args.concurrency,
            warmup_operations=args.warmup_operations,
            timeout_seconds=args.timeout_seconds,
            repetitions=args.repetitions,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for point, report in execution.point_reports:
            (args.output_dir / point.report_file).write_text(
                json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (args.output_dir / "summary.json").write_text(
            json.dumps(execution.summary.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if execution.summary.correctness_passed else 2
    finally:
        if temporary is not None:
            temporary.cleanup()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_run_cli(args))


if __name__ == "__main__":
    raise SystemExit(main())
