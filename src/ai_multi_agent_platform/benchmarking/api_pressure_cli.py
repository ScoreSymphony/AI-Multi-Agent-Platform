"""CLI for authenticated Control Plane API pressure evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig

from .api_pressure import APIPressureBenchmarkSpec, SingleNodeAPIPressureHarness


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-api-pressure")
    parser.add_argument("--seed-tasks", type=int, required=True)
    parser.add_argument("--operations", type=int, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--page-size", type=int, required=True)
    parser.add_argument("--warmup-operations", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--safety-max-seed-tasks", type=int, default=10_000)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    return parser


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(_parser().parse_args(argv)))


async def _run(args: argparse.Namespace) -> int:
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.data_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="ai-map-api-pressure-")
        data_dir = Path(temporary.name) / "data"
    else:
        data_dir = args.data_dir

    try:
        report = await SingleNodeAPIPressureHarness(
            SingleNodeConfig(data_dir=data_dir, secure_cookie=False),
            platform_commit=args.platform_commit,
        ).run(
            APIPressureBenchmarkSpec(
                seed_tasks=args.seed_tasks,
                operation_count=args.operations,
                concurrency=args.concurrency,
                page_size=args.page_size,
                warmup_operations=args.warmup_operations,
                timeout_seconds=args.timeout_seconds,
                safety_max_seed_tasks=args.safety_max_seed_tasks,
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
