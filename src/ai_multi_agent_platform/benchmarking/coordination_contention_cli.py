"""CLI for concurrent multi-Plan and coordinator claim-contention evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import cast

from .coordination_contention import (
    CoordinationContentionHarness,
    CoordinationContentionScenario,
    CoordinationContentionSpec,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-coordination-contention")
    parser.add_argument(
        "--scenario",
        choices=("multi-plan", "claim-contention"),
        required=True,
    )
    parser.add_argument("--plan-count", type=int, required=True)
    parser.add_argument("--steps-per-plan", type=int, required=True)
    parser.add_argument("--claim-hold-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-total-steps", type=int, default=2048)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


async def _run(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-coordination-contention-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await CoordinationContentionHarness(
            data_dir,
            platform_commit=args.platform_commit,
        ).run(
            CoordinationContentionSpec(
                scenario=cast(CoordinationContentionScenario, args.scenario),
                plan_count=args.plan_count,
                steps_per_plan=args.steps_per_plan,
                claim_hold_seconds=args.claim_hold_seconds,
                timeout_seconds=args.timeout_seconds,
                safety_max_total_steps=args.safety_max_total_steps,
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
