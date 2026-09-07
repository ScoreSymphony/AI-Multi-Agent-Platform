"""CLI for distributed Worker loss/rejoin and Workspace failure evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .distributed_faults import DistributedFaultSpec, DistributedWorkerWorkspaceFaultHarness


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-distributed-faults",
        description="Run bounded distributed Worker/Workspace fault-under-load evidence.",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--worker-count", type=int, default=3)
    parser.add_argument("--pre-fault-rounds", type=int, default=1)
    parser.add_argument("--degraded-rounds", type=int, default=1)
    parser.add_argument("--post-rejoin-rounds", type=int, default=1)
    parser.add_argument("--payload-bytes", type=int, default=1024)
    parser.add_argument("--heartbeat-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--reservation-ttl-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-operations", type=int, default=1024)
    parser.add_argument("--safety-max-payload-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        spec = DistributedFaultSpec(
            worker_count=args.worker_count,
            pre_fault_rounds=args.pre_fault_rounds,
            degraded_rounds=args.degraded_rounds,
            post_rejoin_rounds=args.post_rejoin_rounds,
            payload_bytes=args.payload_bytes,
            heartbeat_timeout_seconds=args.heartbeat_timeout_seconds,
            reservation_ttl_seconds=args.reservation_ttl_seconds,
            timeout_seconds=args.timeout_seconds,
            safety_max_operations=args.safety_max_operations,
            safety_max_payload_bytes=args.safety_max_payload_bytes,
        )
    except ValueError as exc:
        print(f"invalid distributed fault benchmark: {exc}")
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    async def execute(data_root: Path) -> int:
        report = await DistributedWorkerWorkspaceFaultHarness(
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
    with tempfile.TemporaryDirectory(prefix="platform-distributed-faults-") as temporary:
        return asyncio.run(execute(Path(temporary) / "data"))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
