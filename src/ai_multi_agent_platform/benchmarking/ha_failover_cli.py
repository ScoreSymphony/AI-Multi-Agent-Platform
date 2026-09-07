"""CLI for deterministic Control Plane HA failover benchmark evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .ha_failover import HAFailoverBenchmarkHarness, HAFailoverBenchmarkSpec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-ha-failover",
        description="Run bounded deterministic Control Plane HA failover benchmark evidence.",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--active-task-count", type=int, default=25)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--lease-ttl-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--safety-max-tasks", type=int, default=1000)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        spec = HAFailoverBenchmarkSpec(
            active_task_count=args.active_task_count,
            repetitions=args.repetitions,
            lease_ttl_seconds=args.lease_ttl_seconds,
            timeout_seconds=args.timeout_seconds,
            safety_max_tasks=args.safety_max_tasks,
        )
    except ValueError as exc:
        print(f"invalid HA failover benchmark: {exc}")
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    async def execute(data_root: Path) -> int:
        report = await HAFailoverBenchmarkHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(spec)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0 if report.correctness.passed else 2

    if args.data_root is not None:
        return asyncio.run(execute(Path(args.data_root)))
    with tempfile.TemporaryDirectory(prefix="platform-ha-failover-") as temporary:
        return asyncio.run(execute(Path(temporary) / "data"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
