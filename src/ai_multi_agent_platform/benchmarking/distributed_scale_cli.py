"""CLI for distributed Worker and remote Workspace scale evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .distributed_scale import DistributedScaleSpec, DistributedWorkerWorkspaceScaleHarness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-distributed-scale",
        description="Run bounded distributed Worker/Workspace scale evidence.",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--worker-count", type=int, default=2)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument(
        "--payload-sizes-bytes",
        default="1024,65536",
        help="Strictly increasing comma-separated remote Workspace payload sizes.",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-operations", type=int, default=2048)
    parser.add_argument("--safety-max-payload-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--output", required=True)
    return parser


def _payload_sizes(value: str) -> tuple[int, ...]:
    try:
        sizes = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("payload sizes must be integers") from exc
    if not sizes:
        raise argparse.ArgumentTypeError("at least one payload size is required")
    return sizes


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        spec = DistributedScaleSpec(
            worker_count=args.worker_count,
            rounds=args.rounds,
            payload_sizes_bytes=_payload_sizes(args.payload_sizes_bytes),
            timeout_seconds=args.timeout_seconds,
            safety_max_operations=args.safety_max_operations,
            safety_max_payload_bytes=args.safety_max_payload_bytes,
        )
    except (ValueError, argparse.ArgumentTypeError) as exc:
        print(f"invalid distributed scale benchmark: {exc}")
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    async def execute(data_root: Path) -> int:
        report = await DistributedWorkerWorkspaceScaleHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(spec)
        output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
        return 0 if report.correctness.passed else 2

    if args.data_root is not None:
        return asyncio.run(execute(Path(args.data_root)))
    with tempfile.TemporaryDirectory(prefix="platform-distributed-scale-") as temporary:
        return asyncio.run(execute(Path(temporary) / "data"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
