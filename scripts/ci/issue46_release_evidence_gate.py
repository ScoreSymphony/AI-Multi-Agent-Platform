"""Validate the transitional #46 release-evidence profile without claiming compatibility.

The operational release profile is intentionally incomplete until scenario G has one
maintained end-to-end failure/retry path. This CI helper still executes every registered
release scenario and fails on real acceptance regressions or on unexpected missing required
coverage. Once G is registered, this transitional helper should be replaced by the normal
release compatibility gate.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ai_multi_agent_platform.conformance import (
    CompatibilityResult,
    ConformanceProfile,
    ConformanceStatus,
    run_conformance,
)

_EXPECTED_PENDING_REQUIRED = frozenset({"G"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issue46-release-evidence-gate")
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path.cwd(),
        help="Repository checkout containing the registered #46 acceptance evidence.",
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=Path("conformance-release-evidence.json"),
        help="Destination for the machine-readable transitional release report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_conformance(
        ConformanceProfile.RELEASE,
        repository_root=args.repository_root,
        deployment_profile="reference-single-node-release-evidence",
    )
    args.json_report.parent.mkdir(parents=True, exist_ok=True)
    args.json_report.write_text(report.to_json(), encoding="utf-8")
    print(report.human_summary())

    failed = {
        result.scenario_id
        for result in report.scenarios
        if result.status == ConformanceStatus.FAIL.value
    }
    if failed:
        print("unexpected failing release scenarios: " + ", ".join(sorted(failed)))
        return 1

    pending_required = {
        result.scenario_id
        for result in report.scenarios
        if result.required and result.status != ConformanceStatus.PASS.value
    }
    if pending_required != _EXPECTED_PENDING_REQUIRED:
        print(
            "unexpected required coverage gap: expected "
            f"{sorted(_EXPECTED_PENDING_REQUIRED)}, got {sorted(pending_required)}"
        )
        return 1

    if report.compatibility_result != CompatibilityResult.INCOMPLETE.value:
        print(
            "transitional release profile must remain incomplete until scenario G is registered"
        )
        return 1

    print("release evidence is healthy; only scenario G remains intentionally incomplete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
