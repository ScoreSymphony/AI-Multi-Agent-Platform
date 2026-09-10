"""CLI for executing bounded reference-host benchmark campaigns."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import cast

from .reference_campaign import (
    ReferenceHostCampaignRunner,
    ReferenceHostCampaignSpec,
    ReferenceHostDescriptor,
    release_campaign_spec,
    smoke_campaign_spec,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-reference-host-campaign")
    parser.add_argument("--profile", choices=("smoke", "release"), required=True)
    parser.add_argument("--host-label", required=True)
    parser.add_argument("--environment-class")
    parser.add_argument("--storage-profile")
    parser.add_argument("--virtualization-profile")
    parser.add_argument("--platform-commit", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--concurrency-levels")
    parser.add_argument("--operations-per-level", type=int)
    parser.add_argument("--warmup-operations", type=int)
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--soak-duration-seconds", type=float)
    parser.add_argument("--soak-sample-interval-seconds", type=float)
    parser.add_argument("--soak-max-operations", type=int)
    parser.add_argument("--soak-concurrency", type=int)
    parser.add_argument("--soak-seed-tasks", type=int)
    parser.add_argument("--soak-warmup-operations", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        spec = _spec_from_args(args)
        host = ReferenceHostDescriptor(
            label=cast(str, args.host_label),
            environment_class=cast(str | None, args.environment_class),
            storage_profile=cast(str | None, args.storage_profile),
            virtualization_profile=cast(str | None, args.virtualization_profile),
        )
        report = asyncio.run(
            ReferenceHostCampaignRunner(
                cast(Path, args.output_dir),
                platform_commit=cast(str, args.platform_commit),
                host=host,
            ).run(spec)
        )
        return 0 if report.correctness_passed else 2
    except (OSError, ValueError) as exc:
        print(f"reference-host campaign failed: {exc}", file=sys.stderr)
        return 2


def _spec_from_args(args: argparse.Namespace) -> ReferenceHostCampaignSpec:
    base = smoke_campaign_spec() if args.profile == "smoke" else release_campaign_spec()
    levels = (
        _parse_levels(cast(str, args.concurrency_levels))
        if args.concurrency_levels is not None
        else base.concurrency_levels
    )
    return ReferenceHostCampaignSpec(
        profile=base.profile,
        concurrency_levels=levels,
        operation_count_per_point=(
            cast(int, args.operations_per_level)
            if args.operations_per_level is not None
            else base.operation_count_per_point
        ),
        warmup_operations=(
            cast(int, args.warmup_operations)
            if args.warmup_operations is not None
            else base.warmup_operations
        ),
        repetitions=(
            cast(int, args.repetitions) if args.repetitions is not None else base.repetitions
        ),
        timeout_seconds=(
            cast(float, args.timeout_seconds)
            if args.timeout_seconds is not None
            else base.timeout_seconds
        ),
        soak_duration_seconds=(
            cast(float, args.soak_duration_seconds)
            if args.soak_duration_seconds is not None
            else base.soak_duration_seconds
        ),
        soak_sample_interval_seconds=(
            cast(float, args.soak_sample_interval_seconds)
            if args.soak_sample_interval_seconds is not None
            else base.soak_sample_interval_seconds
        ),
        soak_max_operations=(
            cast(int, args.soak_max_operations)
            if args.soak_max_operations is not None
            else base.soak_max_operations
        ),
        soak_concurrency=(
            cast(int, args.soak_concurrency)
            if args.soak_concurrency is not None
            else base.soak_concurrency
        ),
        soak_seed_tasks=(
            cast(int, args.soak_seed_tasks)
            if args.soak_seed_tasks is not None
            else base.soak_seed_tasks
        ),
        soak_warmup_operations=(
            cast(int, args.soak_warmup_operations)
            if args.soak_warmup_operations is not None
            else base.soak_warmup_operations
        ),
        soak_read_weight=base.soak_read_weight,
        soak_write_weight=base.soak_write_weight,
    )


def _parse_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("concurrency levels must be comma-separated integers") from exc
    if not levels:
        raise ValueError("at least one concurrency level is required")
    return levels


if __name__ == "__main__":
    raise SystemExit(main())
