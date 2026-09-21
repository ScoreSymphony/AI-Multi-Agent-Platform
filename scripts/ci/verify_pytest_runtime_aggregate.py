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
EXPECTED_VALIDATION_LANES = EXPECTED_LANES | {"quality"}


def _report_run_attempt(
    payload: dict[str, Any],
    *,
    kind: str,
    lane: str,
    path: Path,
) -> int:
    value = payload.get("run_attempt", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"invalid {kind} run_attempt for lane {lane} in {path}: {value!r}")
    return value


def _load_reports(
    root: Path,
    *,
    expected: frozenset[str],
    kind: str,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    attempts: dict[str, int] = {}
    seen_attempts: set[tuple[str, int]] = set()
    for path in sorted(root.rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        lane = payload.get("lane")
        if not isinstance(lane, str) or lane not in expected:
            continue
        attempt = _report_run_attempt(payload, kind=kind, lane=lane, path=path)
        attempt_key = (lane, attempt)
        if attempt_key in seen_attempts:
            raise ValueError(
                f"duplicate {kind} runtime report for lane {lane} at run attempt {attempt}"
            )
        seen_attempts.add(attempt_key)

        current_attempt = attempts.get(lane)
        if current_attempt is not None and attempt < current_attempt:
            continue
        reports[lane] = payload
        attempts[lane] = attempt
    return reports


def load_lane_reports(root: Path) -> dict[str, dict[str, Any]]:
    return _load_reports(root, expected=EXPECTED_LANES, kind="pytest")


def load_validation_reports(root: Path) -> dict[str, dict[str, Any]]:
    return _load_reports(
        root,
        expected=EXPECTED_VALIDATION_LANES,
        kind="validation",
    )


def _require_exact_lanes(
    reports: dict[str, dict[str, Any]],
    expected: frozenset[str],
    *,
    kind: str,
) -> None:
    missing = sorted(expected - reports.keys())
    unexpected = sorted(reports.keys() - expected)
    if not missing and not unexpected:
        return

    details: list[str] = []
    if missing:
        details.append("missing=" + ",".join(missing))
    if unexpected:
        details.append("unexpected=" + ",".join(unexpected))
    raise ValueError(f"invalid {kind} runtime report set: " + "; ".join(details))


def build_aggregate(
    reports: dict[str, dict[str, Any]],
    validation_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    _require_exact_lanes(reports, EXPECTED_LANES, kind="pytest")
    _require_exact_lanes(
        validation_reports,
        EXPECTED_VALIDATION_LANES,
        kind="validation",
    )

    failed_lanes = sorted(
        lane
        for lane, report in reports.items()
        if int(report.get("pytest_exit_code", 1)) != 0 or report.get("budget_violations")
    )
    pytest_wall_seconds = {lane: float(report["wall_seconds"]) for lane, report in reports.items()}
    validation_wall_seconds = {
        lane: float(report["wall_seconds"]) for lane, report in validation_reports.items()
    }
    pytest_critical_lane = max(pytest_wall_seconds, key=pytest_wall_seconds.__getitem__)
    validation_critical_lane = max(
        validation_wall_seconds,
        key=validation_wall_seconds.__getitem__,
    )
    return {
        "schema_version": 1,
        "pytest_lanes": pytest_wall_seconds,
        "pytest_critical_lane": pytest_critical_lane,
        "pytest_critical_path_seconds": round(
            pytest_wall_seconds[pytest_critical_lane],
            3,
        ),
        "pytest_total_compute_seconds": round(sum(pytest_wall_seconds.values()), 3),
        "validation_lanes": validation_wall_seconds,
        "validation_critical_lane": validation_critical_lane,
        "validation_critical_path_seconds": round(
            validation_wall_seconds[validation_critical_lane],
            3,
        ),
        "validation_total_compute_seconds": round(
            sum(validation_wall_seconds.values()),
            3,
        ),
        "failed_lanes": failed_lanes,
    }


def aggregate_violations(
    summary: dict[str, Any],
    budget: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    if summary["failed_lanes"]:
        violations.append("failed or over-budget lanes: " + ", ".join(summary["failed_lanes"]))

    pytest_budget = float(budget["pytest_critical_path_seconds"])
    pytest_path = float(summary["pytest_critical_path_seconds"])
    if pytest_path > pytest_budget:
        violations.append(f"pytest critical path {pytest_path:.3f}s exceeds {pytest_budget:.3f}s")

    validation_budget = float(budget["validation_critical_path_seconds"])
    validation_path = float(summary["validation_critical_path_seconds"])
    if validation_path > validation_budget:
        violations.append(
            f"validation critical path {validation_path:.3f}s exceeds {validation_budget:.3f}s"
        )
    return violations


def render_markdown(
    summary: dict[str, Any],
    budget: dict[str, Any],
    violations: list[str],
) -> str:
    lines = [
        "## Frontend-independent Python validation aggregate",
        "",
        f"- pytest critical lane: **{summary['pytest_critical_lane']}**",
        (
            "- pytest critical path: "
            f"**{summary['pytest_critical_path_seconds']:.3f}s** "
            f"(budget {float(budget['pytest_critical_path_seconds']):.3f}s)"
        ),
        f"- validation critical lane: **{summary['validation_critical_lane']}**",
        (
            "- validation critical path: "
            f"**{summary['validation_critical_path_seconds']:.3f}s** "
            f"(budget {float(budget['validation_critical_path_seconds']):.3f}s)"
        ),
        f"- total pytest compute: **{summary['pytest_total_compute_seconds']:.3f}s**",
        (f"- total validation compute: **{summary['validation_total_compute_seconds']:.3f}s**"),
        "",
        "### Pytest lanes",
        "",
        "| lane | wall seconds |",
        "| --- | ---: |",
    ]
    for lane in sorted(summary["pytest_lanes"]):
        seconds = float(summary["pytest_lanes"][lane])
        lines.append(f"| {lane} | {seconds:.3f} |")

    lines.extend(
        [
            "",
            "### Full Python validation lanes",
            "",
            "| lane | wall seconds |",
            "| --- | ---: |",
        ]
    )
    for lane in sorted(summary["validation_lanes"]):
        seconds = float(summary["validation_lanes"][lane])
        lines.append(f"| {lane} | {seconds:.3f} |")

    lines.extend(["", "### Aggregate budget status", ""])
    if violations:
        lines.extend(f"- FAIL: {violation}" for violation in violations)
    else:
        lines.append("- PASS")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify retained Python lane reports against aggregate CI budgets."
    )
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--validation-reports", type=Path, required=True)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    config = json.loads(args.budget_config.read_text(encoding="utf-8"))
    budget = config["aggregate_budgets"]["frontend-independent-python-ci"]

    try:
        summary = build_aggregate(
            load_lane_reports(args.reports),
            load_validation_reports(args.validation_reports),
        )
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
