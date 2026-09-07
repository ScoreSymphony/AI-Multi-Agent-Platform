"""CLI for deterministic transient persistence-fault benchmark evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from .persistence_faults import PersistenceFaultBenchmarkHarness, PersistenceFaultBenchmarkSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-persistence-fault",
        description=(
            "Measure transient EventRepository failure and idempotent durable recovery "
            "over the SQLite reference persistence path."
        ),
    )
    parser.add_argument("--task-count", type=int, default=24)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--failure-mode",
        choices=("before-commit", "after-commit", "mixed"),
        default="mixed",
    )
    parser.add_argument("--failure-every", type=int, default=3)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-tasks", type=int, default=500)
    parser.add_argument("--safety-max-concurrency", type=int, default=32)
    parser.add_argument("--safety-max-attempts", type=int, default=5)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    spec = PersistenceFaultBenchmarkSpec(
        task_count=args.task_count,
        concurrency=args.concurrency,
        failure_mode=args.failure_mode,
        failure_every=args.failure_every,
        max_attempts=args.max_attempts,
        timeout_seconds=args.timeout_seconds,
        safety_max_tasks=args.safety_max_tasks,
        safety_max_concurrency=args.safety_max_concurrency,
        safety_max_attempts=args.safety_max_attempts,
    )

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-persistence-fault-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await PersistenceFaultBenchmarkHarness(
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


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
