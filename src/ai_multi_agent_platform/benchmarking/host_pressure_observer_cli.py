"""CLI for the read-only dedicated-host pressure observer benchmark."""

from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path

from ai_multi_agent_platform.distributed.linux_pressure import LinuxHostPressureProvider

from .host_pressure_observer import HostPressureObserverHarness, HostPressureObserverSpec


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-host-pressure-observe",
        description=(
            "Observe Linux host-pressure evidence and scheduler admission decisions without "
            "generating pressure or mutating host configuration."
        ),
    )
    parser.add_argument("--duration-seconds", type=float, default=60.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--max-samples", type=int, default=3601)
    parser.add_argument("--safety-max-duration-seconds", type=float, default=3600.0)
    parser.add_argument("--workload-class", default="heavy")
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"))
    parser.add_argument("--sys-root", type=Path, default=Path("/sys"))
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    parser.add_argument("--storage-path", type=Path, default=Path("/"))
    parser.add_argument(
        "--omit-provider-metadata",
        action="store_true",
        help="Keep only portable normalized pressure state/signals in the report.",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--platform-commit",
        default=os.environ.get("GITHUB_SHA", "unknown"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if platform.system() != "Linux":
        parser.error("platform-host-pressure-observe requires Linux pressure sources")

    spec = HostPressureObserverSpec(
        benchmark_id="host-pressure.linux.observer",
        benchmark_version="1.0",
        duration_seconds=args.duration_seconds,
        sample_interval_seconds=args.sample_interval_seconds,
        max_samples=args.max_samples,
        safety_max_duration_seconds=args.safety_max_duration_seconds,
        workload_class=args.workload_class,
        include_provider_metadata=not args.omit_provider_metadata,
    )
    provider = LinuxHostPressureProvider(
        proc_root=args.proc_root,
        sys_root=args.sys_root,
        cgroup_root=args.cgroup_root,
        storage_path=args.storage_path,
    )
    report = HostPressureObserverHarness(
        provider,
        platform_commit=args.platform_commit,
    ).run(spec)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if report.correctness.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
