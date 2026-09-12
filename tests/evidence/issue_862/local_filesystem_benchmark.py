#!/usr/bin/env python3
"""Local-filesystem comparison workload for issue #862 VPS evidence.

The benchmark mirrors the object workload shape used for S3 candidates: one
primary object, a ranged read, listing, eight concurrent write/read round trips,
and independent SHA-256 verification. It intentionally measures the platform's
valid no-object-store path without introducing a resident storage daemon.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from s3_contract_probe import _payload


def _timed(operation):
    started = time.perf_counter()
    result = operation()
    return result, time.perf_counter() - started


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _read(path: Path) -> bytes:
    return path.read_bytes()


def run(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="issue862-local-", dir=root))
    timings: dict[str, float] = {}

    try:
        primary = workdir / "primary.bin"
        data = _payload(f"local:{args.run_id}:primary", args.payload_bytes)
        checksum = hashlib.sha256(data).hexdigest()

        _, timings["put_seconds"] = _timed(lambda: _write(primary, data))
        stat_result, timings["head_seconds"] = _timed(primary.stat)
        if stat_result.st_size != len(data):
            raise RuntimeError("local primary object size mismatch")

        received, timings["get_seconds"] = _timed(lambda: _read(primary))
        if hashlib.sha256(received).hexdigest() != checksum:
            raise RuntimeError("local primary object SHA-256 mismatch")

        range_end = min(len(data), args.range_bytes)

        def ranged_read() -> bytes:
            with primary.open("rb") as handle:
                return handle.read(range_end)

        partial, timings["range_get_seconds"] = _timed(ranged_read)
        if partial != data[:range_end]:
            raise RuntimeError("local ranged read mismatch")

        listed, timings["list_seconds"] = _timed(
            lambda: tuple(path.name for path in workdir.iterdir() if path.is_file())
        )
        if primary.name not in listed:
            raise RuntimeError("local listing omitted primary object")

        def round_trip(index: int) -> dict[str, object]:
            path = workdir / f"concurrent-{index:03d}.bin"
            payload = _payload(
                f"local:{args.run_id}:concurrent:{index}",
                args.concurrent_payload_bytes,
            )
            expected = hashlib.sha256(payload).hexdigest()
            _write(path, payload)
            actual = hashlib.sha256(_read(path)).hexdigest()
            if actual != expected:
                raise RuntimeError(f"local concurrent object {index} SHA-256 mismatch")
            return {"path": path.name, "bytes": len(payload), "sha256": expected}

        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            concurrent_results = list(executor.map(round_trip, range(args.concurrency)))
        timings["concurrent_round_trip_seconds"] = time.perf_counter() - started

        disk_bytes = sum(path.stat().st_size for path in workdir.iterdir() if path.is_file())
        started = time.perf_counter()
        shutil.rmtree(workdir)
        timings["delete_all_seconds"] = time.perf_counter() - started

        return {
            "schema_version": 1,
            "issue": 862,
            "backend": "local_filesystem",
            "run_id": args.run_id,
            "status": "pass",
            "operations": {
                "write": "pass",
                "stat": "pass",
                "read": "pass",
                "range_read": "pass",
                "list": "pass",
                "delete": "pass",
                "concurrent_round_trip": "pass",
            },
            "primary": {
                "bytes": len(data),
                "sha256": checksum,
            },
            "concurrency": args.concurrency,
            "concurrent_objects": concurrent_results,
            "disk_bytes_before_cleanup": disk_bytes,
            "timings_seconds": timings,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--payload-bytes", type=int, default=1024 * 1024)
    parser.add_argument("--range-bytes", type=int, default=4096)
    parser.add_argument("--concurrent-payload-bytes", type=int, default=128 * 1024)
    parser.add_argument("--concurrency", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "issue": 862,
                    "backend": "local_filesystem",
                    "status": "fail",
                    "error": str(exc),
                }
            )
        )
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
