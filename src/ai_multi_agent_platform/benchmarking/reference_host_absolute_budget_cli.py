"""CLI for evidence-backed absolute reference-host performance budgets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from .reference_host_absolute_budget import ReferenceHostAbsoluteBudgetEvaluator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="platform-reference-host-absolute-budget")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _require_distinct_output(*, output: Path, evidence: Path, policy: Path) -> None:
    output_resolved = output.resolve()
    for label, input_path in (("release", evidence), ("policy", policy)):
        if output_resolved == input_path.resolve():
            raise ValueError(f"output must not alias {label} evidence")
        if output.exists() and input_path.exists() and output.samefile(input_path):
            raise ValueError(f"output must not alias {label} evidence")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    evidence = cast(Path, args.evidence)
    policy = cast(Path, args.policy)
    output = cast(Path, args.output)
    try:
        _require_distinct_output(output=output, evidence=evidence, policy=policy)
        report = ReferenceHostAbsoluteBudgetEvaluator().evaluate(
            evidence_path=evidence,
            policy_path=policy,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"reference-host absolute budget evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
