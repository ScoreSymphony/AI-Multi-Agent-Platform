"""CLI for deterministic autonomous-planning pressure evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from .planning_pressure import PlanningPressureBenchmarkHarness, PlanningPressureBenchmarkSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-planning-pressure")
    parser.add_argument("--operations", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--steps-per-plan", type=int, default=3)
    parser.add_argument("--warmup-operations", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-operations", type=int, default=500)
    parser.add_argument("--safety-max-concurrency", type=int, default=64)
    parser.add_argument("--safety-max-steps-per-plan", type=int, default=128)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


async def _run(args: argparse.Namespace) -> int:
    if args.warmup_operations > args.safety_max_operations:
        raise ValueError("warmup_operations exceeds configured planning-pressure safety bound")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-planning-pressure-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await PlanningPressureBenchmarkHarness(
            data_dir,
            platform_commit=args.platform_commit,
        ).run(
            PlanningPressureBenchmarkSpec(
                operation_count=args.operations,
                concurrency=args.concurrency,
                steps_per_plan=args.steps_per_plan,
                warmup_operations=args.warmup_operations,
                timeout_seconds=args.timeout_seconds,
                safety_max_operations=args.safety_max_operations,
                safety_max_concurrency=args.safety_max_concurrency,
                safety_max_steps_per_plan=args.safety_max_steps_per_plan,
            )
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if report.correctness.passed else 2
    finally:
        if temporary is not None:
            temporary.cleanup()
