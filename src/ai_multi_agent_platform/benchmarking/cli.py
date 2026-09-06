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

from .models import BenchmarkSpec, RegressionThresholds, compare_with_baseline
from .single_node import SingleNodeBenchmarkHarness, attach_baseline_comparison


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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command != "single-node":
        raise AssertionError(f"unsupported benchmark command: {args.command}")
    return asyncio.run(_run_single_node(args))


async def _run_single_node(args: argparse.Namespace) -> int:
    thresholds = RegressionThresholds(
        max_p95_latency_regression_ratio=_percentage_ratio(args.max_p95_regression_percent),
        max_throughput_regression_ratio=_percentage_ratio(
            args.max_throughput_regression_percent
        ),
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


if __name__ == "__main__":
    raise SystemExit(main())
