"""Command-line entrypoint for repeatable platform performance benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.deployment import SingleNodeConfig

from .endurance import EnduranceBenchmarkSpec, SingleNodeEnduranceHarness
from .faults import FaultUnderLoadSpec, SingleNodeFaultUnderLoadHarness
from .models import BenchmarkSpec, RegressionThresholds, compare_with_baseline
from .persistence import SingleNodePersistenceScaleHarness
from .single_node import SingleNodeBenchmarkHarness, attach_baseline_comparison
from .stress import SingleNodeStressHarness, StressBenchmarkSpec
from .sweep import SingleNodeSweepHarness
from .transport_faults import TransportFaultBenchmarkHarness, TransportFaultBenchmarkSpec
from .workloads import SingleNodeWorkloadHarness, WorkloadBenchmarkSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)
    single = subparsers.add_parser(
        "single-node",
        help="run the deterministic production-shaped single-node benchmark",
    )
    single.add_argument("--data-dir", type=Path)
    single.add_argument("--operations", type=int, default=10)
    single.add_argument("--concurrency", type=int, default=1)
    single.add_argument("--warmup-operations", type=int, default=1)
    single.add_argument("--timeout-seconds", type=float, default=30.0)
    single.add_argument("--output", type=Path, required=True)
    single.add_argument("--baseline", type=Path)
    single.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    single.add_argument("--max-p95-regression-percent", type=float)
    single.add_argument("--max-throughput-regression-percent", type=float)
    single.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="return a non-zero exit code when an explicit configured budget is exceeded",
    )

    sweep = subparsers.add_parser(
        "single-node-sweep",
        help="run independent deterministic lifecycle benchmarks across concurrency levels",
    )
    sweep.add_argument("--data-root", type=Path)
    sweep.add_argument("--concurrency-levels", default="1,10,50,100")
    sweep.add_argument("--operations-per-level", type=int, default=100)
    sweep.add_argument("--warmup-operations", type=int, default=5)
    sweep.add_argument("--repetitions", type=int, default=1)
    sweep.add_argument("--timeout-seconds", type=float, default=30.0)
    sweep.add_argument("--output-dir", type=Path, required=True)
    sweep.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))

    persistence_sweep = subparsers.add_parser(
        "single-node-persistence-sweep",
        help="measure query, storage and restart behavior as durable state grows",
    )
    persistence_sweep.add_argument("--data-root", type=Path)
    persistence_sweep.add_argument("--seed-task-levels", default="10,100,1000")
    persistence_sweep.add_argument("--operations-per-level", type=int, default=100)
    persistence_sweep.add_argument("--concurrency", type=int, default=10)
    persistence_sweep.add_argument("--warmup-operations", type=int, default=5)
    persistence_sweep.add_argument("--repetitions", type=int, default=1)
    persistence_sweep.add_argument("--timeout-seconds", type=float, default=30.0)
    persistence_sweep.add_argument("--output-dir", type=Path, required=True)
    persistence_sweep.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )

    workload = subparsers.add_parser(
        "single-node-workload",
        help="run read-heavy, mixed, history or restart single-node workloads",
    )
    workload.add_argument(
        "--scenario",
        choices=("read-heavy", "mixed", "history", "restart"),
        required=True,
    )
    workload.add_argument("--data-dir", type=Path)
    workload.add_argument("--operations", type=int, default=100)
    workload.add_argument("--concurrency", type=int, default=10)
    workload.add_argument("--seed-tasks", type=int)
    workload.add_argument("--warmup-operations", type=int, default=5)
    workload.add_argument("--timeout-seconds", type=float, default=30.0)
    workload.add_argument("--read-weight", type=int, default=4)
    workload.add_argument("--write-weight", type=int, default=1)
    workload.add_argument("--output", type=Path, required=True)
    workload.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))

    endurance = subparsers.add_parser(
        "single-node-endurance",
        help="measure idle footprint or bounded single-node soak stability",
    )
    endurance.add_argument("--scenario", choices=("idle", "soak"), required=True)
    endurance.add_argument("--data-dir", type=Path)
    endurance.add_argument("--duration-seconds", type=float, default=300.0)
    endurance.add_argument("--sample-interval-seconds", type=float, default=10.0)
    endurance.add_argument("--max-operations", type=int)
    endurance.add_argument("--concurrency", type=int)
    endurance.add_argument("--seed-tasks", type=int)
    endurance.add_argument("--warmup-operations", type=int)
    endurance.add_argument("--timeout-seconds", type=float, default=30.0)
    endurance.add_argument("--read-weight", type=int)
    endurance.add_argument("--write-weight", type=int)
    endurance.add_argument("--output", type=Path, required=True)
    endurance.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))

    stress = subparsers.add_parser(
        "single-node-stress",
        help="run an explicitly bounded concurrency-saturation sweep",
    )
    stress.add_argument("--data-root", type=Path)
    stress.add_argument("--concurrency-levels", default="10,25,50,100")
    stress.add_argument("--operations-per-level", type=int, default=100)
    stress.add_argument("--warmup-operations", type=int, default=5)
    stress.add_argument("--timeout-seconds", type=float, default=30.0)
    stress.add_argument("--safety-max-concurrency", type=int, default=256)
    stress.add_argument("--safety-max-operations-per-level", type=int, default=1000)
    stress.add_argument("--continue-after-correctness-failure", action="store_true")
    stress.add_argument("--output-dir", type=Path, required=True)
    stress.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))

    fault = subparsers.add_parser(
        "single-node-fault-under-load",
        help="run bounded load before and after a real single-node deployment restart",
    )
    fault.add_argument("--data-dir", type=Path)
    fault.add_argument("--operations", type=int, default=40)
    fault.add_argument("--concurrency", type=int, default=4)
    fault.add_argument("--fault-after-operations", type=int)
    fault.add_argument("--seed-tasks", type=int, default=10)
    fault.add_argument("--warmup-operations", type=int, default=2)
    fault.add_argument("--timeout-seconds", type=float, default=30.0)
    fault.add_argument("--safety-max-operations", type=int, default=1000)
    fault.add_argument("--safety-max-concurrency", type=int, default=64)
    fault.add_argument("--read-weight", type=int, default=4)
    fault.add_argument("--write-weight", type=int, default=1)
    fault.add_argument("--output", type=Path, required=True)
    fault.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))

    transport_fault = subparsers.add_parser(
        "transport-fault",
        help="run bounded in-process transport backpressure, outage or duplicate-delivery evidence",
    )
    transport_fault.add_argument(
        "--scenario",
        choices=("backpressure", "outage", "duplicate-delivery"),
        required=True,
    )
    transport_fault.add_argument("--batch-size", type=int, default=10)
    transport_fault.add_argument("--concurrency", type=int, default=4)
    transport_fault.add_argument("--max-queue-size", type=int)
    transport_fault.add_argument("--fault-operations", type=int)
    transport_fault.add_argument("--timeout-seconds", type=float, default=30.0)
    transport_fault.add_argument("--output", type=Path, required=True)
    transport_fault.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "single-node":
        return asyncio.run(_run_single_node(args))
    if args.command == "single-node-sweep":
        return asyncio.run(_run_single_node_sweep(args))
    if args.command == "single-node-persistence-sweep":
        return asyncio.run(_run_single_node_persistence_sweep(args))
    if args.command == "single-node-workload":
        return asyncio.run(_run_single_node_workload(args))
    if args.command == "single-node-endurance":
        return asyncio.run(_run_single_node_endurance(args))
    if args.command == "single-node-stress":
        return asyncio.run(_run_single_node_stress(args))
    if args.command == "single-node-fault-under-load":
        return asyncio.run(_run_single_node_fault_under_load(args))
    if args.command == "transport-fault":
        return asyncio.run(_run_transport_fault(args))
    raise AssertionError(f"unsupported benchmark command: {args.command}")


async def _run_single_node(args: argparse.Namespace) -> int:
    thresholds = RegressionThresholds(
        max_p95_latency_regression_ratio=_percentage_ratio(args.max_p95_regression_percent),
        max_throughput_regression_ratio=_percentage_ratio(args.max_throughput_regression_percent),
    )
    spec = BenchmarkSpec(
        benchmark_id="single-node.reference.lifecycle",
        benchmark_version="1.0",
        deployment_profile="single-node-reference",
        operation_count=args.operations,
        concurrency=args.concurrency,
        warmup_operations=args.warmup_operations,
        timeout_seconds=args.timeout_seconds,
    )

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-")
        data_dir = Path(temporary.name)
    else:
        data_dir = args.data_dir

    try:
        config = SingleNodeConfig(data_dir=data_dir, secure_cookie=False)
        report = await SingleNodeBenchmarkHarness(
            config,
            platform_commit=args.platform_commit,
        ).run(spec)
        if args.baseline is not None:
            baseline = _load_json_object(args.baseline)
            comparison = compare_with_baseline(report, baseline, thresholds)
            report = attach_baseline_comparison(report, comparison)

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not report.correctness.passed:
            return 2
        if (
            args.fail_on_regression
            and report.baseline_comparison is not None
            and report.baseline_comparison.classification == "regression"
        ):
            return 3
        return 0
    finally:
        if temporary is not None:
            temporary.cleanup()


async def _run_single_node_sweep(args: argparse.Namespace) -> int:
    concurrency_levels = _parse_concurrency_levels(args.concurrency_levels)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-sweep-")
        data_root = Path(temporary.name)
    else:
        data_root = args.data_root

    try:
        execution = await SingleNodeSweepHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(
            concurrency_levels=concurrency_levels,
            operation_count=args.operations_per_level,
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


async def _run_single_node_persistence_sweep(args: argparse.Namespace) -> int:
    seed_task_levels = _parse_seed_task_levels(args.seed_task_levels)
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
            seed_task_levels=seed_task_levels,
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


async def _run_single_node_workload(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-workload-")
        data_dir = Path(temporary.name)
    else:
        data_dir = args.data_dir

    seed_tasks = args.seed_tasks
    if seed_tasks is None:
        seed_tasks = 1000 if args.scenario == "history" else 50
    benchmark_id, distribution = _workload_identity(args.scenario)
    read_weight = args.read_weight if args.scenario == "mixed" else 1
    write_weight = args.write_weight if args.scenario == "mixed" else 0
    spec = WorkloadBenchmarkSpec(
        benchmark_id=benchmark_id,
        benchmark_version="1.0",
        scenario=args.scenario,
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        workload_distribution=distribution,
        operation_count=args.operations,
        concurrency=args.concurrency,
        seed_tasks=seed_tasks,
        warmup_operations=args.warmup_operations,
        timeout_seconds=args.timeout_seconds,
        read_weight=read_weight,
        write_weight=write_weight,
    )
    try:
        report = await SingleNodeWorkloadHarness(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
            platform_commit=args.platform_commit,
        ).run(spec)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if report.correctness.passed else 2
    finally:
        if temporary is not None:
            temporary.cleanup()


async def _run_single_node_endurance(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-endurance-")
        data_dir = Path(temporary.name)
    else:
        data_dir = args.data_dir

    if args.scenario == "idle":
        benchmark_id = "single-node.idle.footprint"
        max_operations = 0 if args.max_operations is None else args.max_operations
        concurrency = 1 if args.concurrency is None else args.concurrency
        seed_tasks = 0 if args.seed_tasks is None else args.seed_tasks
        warmup_operations = 0 if args.warmup_operations is None else args.warmup_operations
        read_weight = 0 if args.read_weight is None else args.read_weight
        write_weight = 0 if args.write_weight is None else args.write_weight
    else:
        benchmark_id = "single-node.soak.mixed"
        max_operations = 10000 if args.max_operations is None else args.max_operations
        concurrency = 4 if args.concurrency is None else args.concurrency
        seed_tasks = 10 if args.seed_tasks is None else args.seed_tasks
        warmup_operations = 5 if args.warmup_operations is None else args.warmup_operations
        read_weight = 4 if args.read_weight is None else args.read_weight
        write_weight = 1 if args.write_weight is None else args.write_weight

    spec = EnduranceBenchmarkSpec(
        benchmark_id=benchmark_id,
        benchmark_version="1.0",
        scenario=args.scenario,
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        duration_seconds=args.duration_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        max_operations=max_operations,
        concurrency=concurrency,
        seed_tasks=seed_tasks,
        warmup_operations=warmup_operations,
        timeout_seconds=args.timeout_seconds,
        read_weight=read_weight,
        write_weight=write_weight,
    )
    try:
        report = await SingleNodeEnduranceHarness(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
            platform_commit=args.platform_commit,
        ).run(spec)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if report.correctness.passed else 2
    finally:
        if temporary is not None:
            temporary.cleanup()


async def _run_single_node_stress(args: argparse.Namespace) -> int:
    concurrency_levels = _parse_concurrency_levels(args.concurrency_levels)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-stress-")
        data_root = Path(temporary.name)
    else:
        data_root = args.data_root

    spec = StressBenchmarkSpec(
        benchmark_id="single-node.reference.lifecycle.stress",
        benchmark_version="1.0",
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        concurrency_levels=concurrency_levels,
        operations_per_level=args.operations_per_level,
        warmup_operations=args.warmup_operations,
        timeout_seconds=args.timeout_seconds,
        safety_max_concurrency=args.safety_max_concurrency,
        safety_max_operations_per_level=args.safety_max_operations_per_level,
        stop_on_correctness_failure=not args.continue_after_correctness_failure,
    )
    try:
        execution = await SingleNodeStressHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(spec)
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


async def _run_single_node_fault_under_load(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-benchmark-fault-")
        data_dir = Path(temporary.name)
    else:
        data_dir = args.data_dir

    fault_after = args.fault_after_operations
    if fault_after is None:
        fault_after = max(1, args.operations // 2)
    spec = FaultUnderLoadSpec(
        benchmark_id="single-node.fault.control-plane-restart-under-load",
        benchmark_version="1.0",
        scenario="control-plane-restart",
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        operation_count=args.operations,
        concurrency=args.concurrency,
        fault_after_operations=fault_after,
        seed_tasks=args.seed_tasks,
        warmup_operations=args.warmup_operations,
        timeout_seconds=args.timeout_seconds,
        safety_max_operations=args.safety_max_operations,
        safety_max_concurrency=args.safety_max_concurrency,
        read_weight=args.read_weight,
        write_weight=args.write_weight,
    )
    try:
        report = await SingleNodeFaultUnderLoadHarness(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
            platform_commit=args.platform_commit,
        ).run_fault(spec)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if report.correctness.passed else 2
    finally:
        if temporary is not None:
            temporary.cleanup()


async def _run_transport_fault(args: argparse.Namespace) -> int:
    if args.scenario == "backpressure":
        max_queue_size = args.batch_size if args.max_queue_size is None else args.max_queue_size
        fault_operations = 1 if args.fault_operations is None else args.fault_operations
    elif args.scenario == "outage":
        max_queue_size = args.batch_size * 2 if args.max_queue_size is None else args.max_queue_size
        fault_operations = (
            args.batch_size if args.fault_operations is None else args.fault_operations
        )
    else:
        max_queue_size = args.batch_size + 1 if args.max_queue_size is None else args.max_queue_size
        fault_operations = 0 if args.fault_operations is None else args.fault_operations

    spec = TransportFaultBenchmarkSpec(
        benchmark_id=f"transport.reference.{args.scenario}",
        benchmark_version="1.0",
        scenario=args.scenario,
        transport_profile="in-process-reference",
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        max_queue_size=max_queue_size,
        fault_operations=fault_operations,
        timeout_seconds=args.timeout_seconds,
    )
    report = await TransportFaultBenchmarkHarness(platform_commit=args.platform_commit).run(spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report.correctness.passed else 2


def _workload_identity(scenario: str) -> tuple[str, str]:
    identities = {
        "read-heavy": ("single-node.api.read-heavy", "list-detail-runs-timeline"),
        "mixed": ("single-node.api.mixed", "weighted-read-write-lifecycle"),
        "history": ("single-node.api.history", "large-state-query-mix"),
        "restart": ("single-node.restart.accumulated-state", "restart-then-query"),
    }
    try:
        return identities[scenario]
    except KeyError as exc:
        raise ValueError(f"unsupported workload scenario: {scenario}") from exc


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"benchmark baseline must be a JSON object: {path}")
    return payload


def _percentage_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 0:
        raise ValueError("regression percentages must not be negative")
    return value / 100.0


def _parse_concurrency_levels(value: str) -> tuple[int, ...]:
    fields = tuple(field.strip() for field in value.split(",") if field.strip())
    if not fields:
        raise ValueError("at least one concurrency level is required")
    try:
        levels = tuple(int(field) for field in fields)
    except ValueError as exc:
        raise ValueError("concurrency levels must be comma-separated integers") from exc
    if any(level < 1 for level in levels):
        raise ValueError("concurrency levels must be positive")
    if len(set(levels)) != len(levels):
        raise ValueError("concurrency levels must be unique")
    return levels


def _parse_seed_task_levels(value: str) -> tuple[int, ...]:
    fields = tuple(field.strip() for field in value.split(",") if field.strip())
    if not fields:
        raise ValueError("at least one seed-task level is required")
    try:
        levels = tuple(int(field) for field in fields)
    except ValueError as exc:
        raise ValueError("seed-task levels must be comma-separated integers") from exc
    if any(level < 1 for level in levels):
        raise ValueError("seed-task levels must be positive")
    if len(set(levels)) != len(levels):
        raise ValueError("seed-task levels must be unique")
    if tuple(sorted(levels)) != levels:
        raise ValueError("seed-task levels must be strictly increasing")
    return levels


if __name__ == "__main__":
    raise SystemExit(main())
