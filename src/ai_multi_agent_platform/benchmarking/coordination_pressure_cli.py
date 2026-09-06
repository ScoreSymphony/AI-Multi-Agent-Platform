"""CLI for durable retry, wait and restart/reconciliation pressure evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import cast

from .coordination_pressure import (
    CoordinationPressureHarness,
    CoordinationPressureScenario,
    CoordinationPressureSpec,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-coordination-pressure")
    parser.add_argument(
        "--scenario",
        choices=("retry-burst", "deadline-wait-burst", "restart-reconcile"),
        required=True,
    )
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--retry-delay-seconds", type=float, default=1.0)
    parser.add_argument("--wait-delay-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-size", type=int, default=1024)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


async def _run(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-coordination-pressure-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await CoordinationPressureHarness(
            data_dir,
            platform_commit=args.platform_commit,
        ).run(
            CoordinationPressureSpec(
                scenario=cast(CoordinationPressureScenario, args.scenario),
                size=args.size,
                retry_delay_seconds=args.retry_delay_seconds,
                wait_delay_seconds=args.wait_delay_seconds,
                timeout_seconds=args.timeout_seconds,
                safety_max_size=args.safety_max_size,
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
