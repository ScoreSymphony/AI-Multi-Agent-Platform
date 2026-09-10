"""CLI for evidence-backed reference-host performance regression classification."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from .reference_host_regression import ReferenceHostRegressionComparator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-reference-host-regression")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    baseline = cast(Path, args.baseline)
    candidate = cast(Path, args.candidate)
    policy = cast(Path, args.policy)
    output = cast(Path, args.output)
    try:
        report = ReferenceHostRegressionComparator().compare(
            baseline_path=baseline,
            candidate_path=candidate,
            policy_path=policy,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"reference-host regression comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
