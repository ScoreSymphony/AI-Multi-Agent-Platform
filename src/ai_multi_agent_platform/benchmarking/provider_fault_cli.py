"""CLI for deterministic model/tool/provider degradation benchmarks."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from .provider_faults import ProviderFaultBenchmarkHarness, ProviderFaultBenchmarkSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-provider-fault",
        description=(
            "Measure canonical model/tool provider degradation, timeout, cancellation "
            "and recovery under bounded concurrent load."
        ),
    )
    parser.add_argument(
        "--scenario",
        choices=(
            "model-latency",
            "model-unavailable",
            "model-cancelled",
            "tool-unavailable",
            "tool-timeout",
            "tool-cancelled",
        ),
        required=True,
    )
    parser.add_argument("--operations-per-phase", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--fault-delay-seconds", type=float, default=0.05)
    parser.add_argument("--tool-timeout-seconds", type=float, default=0.01)
    parser.add_argument("--cancel-after-seconds", type=float, default=0.01)
    parser.add_argument("--operation-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--safety-max-operations-per-phase", type=int, default=1000)
    parser.add_argument("--safety-max-concurrency", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    spec = ProviderFaultBenchmarkSpec(
        benchmark_id=f"provider.reference.{args.scenario}",
        benchmark_version="1.0",
        scenario=args.scenario,
        operations_per_phase=args.operations_per_phase,
        concurrency=args.concurrency,
        fault_delay_seconds=args.fault_delay_seconds,
        tool_timeout_seconds=args.tool_timeout_seconds,
        cancel_after_seconds=args.cancel_after_seconds,
        operation_timeout_seconds=args.operation_timeout_seconds,
        safety_max_operations_per_phase=args.safety_max_operations_per_phase,
        safety_max_concurrency=args.safety_max_concurrency,
    )
    report = await ProviderFaultBenchmarkHarness(platform_commit=args.platform_commit).run(spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report.correctness.passed else 2


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
