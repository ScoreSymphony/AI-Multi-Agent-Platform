"""CLI for deterministic large-Plan graph scale evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from .planning_graph_scale import (
    PlanningGraphScaleBenchmarkHarness,
    PlanningGraphScaleBenchmarkSpec,
)


def _step_counts(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("step counts must be comma-separated integers") from exc
    if not parsed:
        raise argparse.ArgumentTypeError("step counts must not be empty")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-planning-graph-scale")
    parser.add_argument("--step-counts", type=_step_counts, default=(10, 100, 1000))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--warmup-repetitions", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--safety-max-steps-per-plan", type=int, default=2048)
    parser.add_argument("--safety-max-repetitions", type=int, default=20)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


async def _run(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-planning-graph-scale-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        spec = PlanningGraphScaleBenchmarkSpec(
            step_counts=args.step_counts,
            repetitions=args.repetitions,
            warmup_repetitions=args.warmup_repetitions,
            timeout_seconds=args.timeout_seconds,
            safety_max_steps_per_plan=args.safety_max_steps_per_plan,
            safety_max_repetitions=args.safety_max_repetitions,
        )
        report = await PlanningGraphScaleBenchmarkHarness(
            data_dir,
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


__all__ = ["main"]
