from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "python-test-runtime.json"
EXPECTED_LANES = frozenset(
    {
        "unit",
        "contract-architecture-release",
        "integration",
        "system-regression",
    }
)


def load_lane_reports(root: Path) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        lane = payload.get("lane")
        if not isinstance(lane, str) or lane not in EXPECTED_LANES:
            continue
        if lane in reports:
            raise ValueError(f"duplicate runtime report for lane: {lane}")
        reports[lane] = payload
    return reports


def build_aggregate(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    missing = sorted(EXPECTED_LANES - reports.keys())
    unexpected = sorted(reports.keys() - EXPECTED_LANES)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ValueError("invalid runtime report set: " + "; ".join(details))

    failed_lanes = sorted(
        lane
        for lane, report in reports.items()
        if int(report.get("pytest_exit_code", 1)) != 0 or report.get("budget_violations")
    )
    wall_seconds = {
        lane: float(report["wall_seconds"])
        for lane, report in reports.items()
    }
    critical_lane = max(wall_seconds, key=wall_seconds.__getitem__)
    return {
        "schema_version": 1,
        "lanes": wall_seconds,
        "critical_lane": critical_lane,
        "pytest_critical_path_seconds": round(wall_seconds[critical_lane], 3),
        "pytest_total_compute_seconds": round(sum(wall_seconds.values()), 3),
        "failed_lanes": failed_lanes,
    }


def aggregate_violations(
    summary: dict[str, Any],
    budget: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    if summary["failed_lanes"]:
        violations.append(
            "failed or over-budget lanes: " + ", ".join(summary["failed_lanes"])
        )

    critical_budget = float(budget["pytest_critical_path_seconds"])
    critical_path = float(summary["pytest_critical_path_seconds"])
    if critical_path > critical_budget:
        violations.append(
            f"pytest critical path {critical_path:.3f}s exceeds "
            f"{critical_budget:.3f}s"
        )
    return violations


def render_markdown(
    summary: dict[str, Any],
    budget: dict[str, Any],
    violations: list[str],
) -> str:
    lines = [
        "## Frontend-independent Python test aggregate",
        "",
        f"- critical lane: **{summary['critical_lane']}**",
        (
            "- pytest critical path: "
            f"**{summary['pytest_critical_path_seconds']:.3f}s** "
            f"(budget {float(budget['pytest_critical_path_seconds']):.3f}s)"
        ),
        f"- total pytest compute: **{summary['pytest_total_compute_seconds']:.3f}s**",
        "",
        "| lane | wall seconds |",
        "| --- | ---: |",
    ]
    for lane in sorted(summary["lanes"]):
        lines.append(f"| {lane} | {float(summary['lanes'][lane]):.3f} |")

    lines.extend(["", "### Aggregate budget status", ""])
    if violations:
        lines.extend(f"- FAIL: {violation}" for violation in violations)
    else:
        lines.append("- PASS")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify retained pytest lane reports against aggregate CI budgets."
    )
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = json.loads(args.budget_config.read_text(encoding="utf-8"))
    budget = config["aggregate_budgets"]["frontend-independent-python-ci"]

    try:
        summary = build_aggregate(load_lane_reports(args.reports))
    except (OSError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"runtime aggregate verification failed: {exc}", file=sys.stderr)
        return 2

    violations = aggregate_violations(summary, budget)
    summary["budget"] = budget
    summary["budget_violations"] = violations
    markdown = render_markdown(summary, budget, violations)

    output = args.output or (args.reports / "aggregate.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(markdown)

    print(markdown, end="")
    if violations:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
