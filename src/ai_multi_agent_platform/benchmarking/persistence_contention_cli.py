"""CLI for real SQLite writer-contention benchmark evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from .persistence_contention import (
    PersistenceContentionBenchmarkHarness,
    PersistenceContentionBenchmarkSpec,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-persistence-contention",
        description=(
            "Measure synchronized competing canonical writers against the SQLite reference "
            "persistence path without benchmark-only database mutations."
        ),
    )
    parser.add_argument("--writers", type=int, default=4)
    parser.add_argument("--tasks-per-writer", type=int, default=8)
    parser.add_argument("--barrier-timeout-seconds", type=float, default=10.0)
    parser.add_argument("--safety-max-writers", type=int, default=16)
    parser.add_argument("--safety-max-tasks-per-writer", type=int, default=50)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )
    return parser


async def _run(args: argparse.Namespace) -> int:
    spec = PersistenceContentionBenchmarkSpec(
        writer_count=args.writers,
        tasks_per_writer=args.tasks_per_writer,
        barrier_timeout_seconds=args.barrier_timeout_seconds,
        safety_max_writers=args.safety_max_writers,
        safety_max_tasks_per_writer=args.safety_max_tasks_per_writer,
    )

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-persistence-contention-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await PersistenceContentionBenchmarkHarness(
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
