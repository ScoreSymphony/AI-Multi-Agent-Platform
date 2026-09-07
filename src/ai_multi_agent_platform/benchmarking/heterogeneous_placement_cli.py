"""CLI for heterogeneous capability/resource placement benchmark evidence."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from .heterogeneous_placement import (
    HeterogeneousPlacementBenchmarkHarness,
    HeterogeneousPlacementSpec,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="platform-heterogeneous-placement",
        description="Run bounded heterogeneous Worker placement benchmark evidence.",
    )
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--iterations-per-profile", type=int, default=100)
    parser.add_argument("--safety-max-operations", type=int, default=10_000)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA", "unknown"))
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        spec = HeterogeneousPlacementSpec(
            iterations_per_profile=args.iterations_per_profile,
            safety_max_operations=args.safety_max_operations,
        )
    except ValueError as exc:
        print(f"invalid heterogeneous placement benchmark: {exc}")
        return 2

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    def execute(data_root: Path) -> int:
        report = HeterogeneousPlacementBenchmarkHarness(
            data_root,
            platform_commit=args.platform_commit,
        ).run(spec)
        output.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
        return 0 if report.correctness.passed else 2

    if args.data_root is not None:
        return execute(Path(args.data_root))
    with tempfile.TemporaryDirectory(prefix="platform-heterogeneous-placement-") as temporary:
        return execute(Path(temporary) / "data")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
