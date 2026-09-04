"""Run the checked-in deterministic #19 evaluation gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.evaluation import run_reference_ci_gate

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SUITE = _REPOSITORY_ROOT / "config" / "evaluation-suite.pr-deterministic.json"
_DEFAULT_POLICY = _REPOSITORY_ROOT / "config" / "evaluation-regression.pr-deterministic.json"
_DEFAULT_BASELINE = _REPOSITORY_ROOT / "config" / "evaluation-baseline.pr-deterministic.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the deterministic no-paid-service evaluation regression gate",
    )
    parser.add_argument("--suite", type=Path, default=_DEFAULT_SUITE)
    parser.add_argument("--policy", type=Path, default=_DEFAULT_POLICY)
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument("--platform-version", default=__version__)
    parser.add_argument("--platform-commit", default=os.environ.get("GITHUB_SHA"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with TemporaryDirectory(prefix="ai-platform-evaluation-ci-") as temporary_root:
            report = asyncio.run(
                run_reference_ci_gate(
                    suite_path=args.suite,
                    policy_path=args.policy,
                    baseline_path=args.baseline,
                    workspace_root=temporary_root,
                    platform_version=args.platform_version,
                    platform_commit=args.platform_commit,
                )
            )
    except (OSError, ValueError) as exc:
        print(f"evaluation gate configuration/execution error: {exc}", file=sys.stderr)
        return 2

    comparison = report.summary.comparison
    payload = {
        "passed": report.passed,
        "suite_ref": f"{report.suite.suite_id}@{report.suite.version}",
        "policy_ref": f"{report.policy.policy_id}@{report.policy.version}",
        "baseline_run_id": report.baseline.run.run_id,
        "current_run_id": report.summary.run.run_id,
        "failed_results": len(report.failed_results),
        "regressions": len(report.regressions),
        "improvements": 0 if comparison is None else len(comparison.improvements),
        "platform_version": report.summary.run.snapshot.platform_version,
        "platform_commit": report.summary.run.snapshot.platform_commit,
    }
    print(json.dumps(payload, sort_keys=True))

    if report.passed:
        return 0
    for diagnostic in report.diagnostics():
        print(diagnostic, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
