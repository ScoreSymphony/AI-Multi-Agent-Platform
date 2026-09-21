from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "python-test-runtime.json"
DEFAULT_OUTPUT_DIR = ROOT / ".artifacts" / "test-runtime"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one pytest lane, retain timing evidence and enforce runtime budgets."
    )
    parser.add_argument("--lane", required=True)
    parser.add_argument("--budget-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def _load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _testcases(junit_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(junit_path).getroot()
    cases: list[dict[str, Any]] = []
    for case in root.iter("testcase"):
        classname = case.attrib.get("classname", "<unknown>")
        name = case.attrib.get("name", "<unknown>")
        duration = float(case.attrib.get("time", "0") or 0)
        cases.append(
            {
                "node": f"{classname}::{name}",
                "module": classname,
                "seconds": duration,
                "failed": case.find("failure") is not None or case.find("error") is not None,
                "skipped": case.find("skipped") is not None,
            }
        )
    return cases


def build_report(
    *,
    lane: str,
    wall_seconds: float,
    junit_path: Path,
    budget: dict[str, Any],
    exit_code: int,
) -> dict[str, Any]:
    cases = _testcases(junit_path)
    modules: dict[str, float] = defaultdict(float)
    directories: dict[str, float] = defaultdict(float)
    for case in cases:
        module = str(case["module"])
        seconds = float(case["seconds"])
        modules[module] += seconds
        parts = module.split(".")
        directory = "/".join(parts[:2]) if len(parts) >= 2 and parts[0] == "tests" else parts[0]
        directories[directory] += seconds

    slow_tests = sorted(cases, key=lambda item: float(item["seconds"]), reverse=True)[:25]
    slow_modules = sorted(
        ({"module": name, "seconds": seconds} for name, seconds in modules.items()),
        key=lambda item: float(item["seconds"]),
        reverse=True,
    )[:25]
    slow_directories = sorted(
        ({"directory": name, "seconds": seconds} for name, seconds in directories.items()),
        key=lambda item: float(item["seconds"]),
        reverse=True,
    )[:25]

    return {
        "schema_version": 1,
        "lane": lane,
        "wall_seconds": round(wall_seconds, 3),
        "pytest_exit_code": exit_code,
        "testcase_count": len(cases),
        "failed_count": sum(1 for case in cases if case["failed"]),
        "skipped_count": sum(1 for case in cases if case["skipped"]),
        "testcase_seconds_sum": round(sum(float(case["seconds"]) for case in cases), 3),
        "slowest_tests": slow_tests,
        "slowest_modules": slow_modules,
        "slowest_directories": slow_directories,
        "budget": budget,
    }


def budget_violations(report: dict[str, Any]) -> list[str]:
    budget = report["budget"]
    violations: list[str] = []

    wall_budget = budget.get("wall_seconds")
    if wall_budget is not None and report["wall_seconds"] > wall_budget:
        violations.append(
            f"lane wall time {report['wall_seconds']:.3f}s exceeds {float(wall_budget):.3f}s"
        )

    test_budget = budget.get("slow_test_seconds")
    if test_budget is not None and report["slowest_tests"]:
        slowest = report["slowest_tests"][0]
        if slowest["seconds"] > test_budget:
            violations.append(
                f"slowest test {slowest['node']} took {slowest['seconds']:.3f}s "
                f"(budget {float(test_budget):.3f}s)"
            )

    module_budget = budget.get("slow_module_seconds")
    if module_budget is not None and report["slowest_modules"]:
        slowest = report["slowest_modules"][0]
        if slowest["seconds"] > module_budget:
            violations.append(
                f"slowest module {slowest['module']} took {slowest['seconds']:.3f}s "
                f"(budget {float(module_budget):.3f}s)"
            )

    return violations


def render_markdown(report: dict[str, Any], violations: list[str]) -> str:
    lines = [
        f"## Python test runtime: {report['lane']}",
        "",
        f"- wall clock: **{report['wall_seconds']:.3f}s**",
        f"- collected testcases: **{report['testcase_count']}**",
        f"- failed: **{report['failed_count']}**",
        f"- skipped: **{report['skipped_count']}**",
        f"- summed testcase time: **{report['testcase_seconds_sum']:.3f}s**",
        "",
        "### Slowest tests",
        "",
        "| seconds | test |",
        "| ---: | --- |",
    ]
    for item in report["slowest_tests"][:15]:
        lines.append(f"| {item['seconds']:.3f} | `{item['node']}` |")

    lines.extend(
        [
            "",
            "### Slowest modules/classes",
            "",
            "| seconds | module/class |",
            "| ---: | --- |",
        ]
    )
    for item in report["slowest_modules"][:15]:
        lines.append(f"| {item['seconds']:.3f} | `{item['module']}` |")

    lines.extend(
        [
            "",
            "### Slowest test directories",
            "",
            "| seconds | directory |",
            "| ---: | --- |",
        ]
    )
    for item in report["slowest_directories"][:15]:
        lines.append(f"| {item['seconds']:.3f} | `{item['directory']}` |")

    lines.extend(["", "### Budget status", ""])
    if violations:
        lines.extend(f"- FAIL: {violation}" for violation in violations)
    else:
        lines.append("- PASS")

    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parser().parse_args()
    pytest_args = list(args.pytest_args)
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    if not pytest_args:
        raise SystemExit("pytest arguments are required after --")

    config = _load_config(args.budget_config)
    try:
        budget = config["lane_budgets"][args.lane]
    except KeyError as exc:
        raise SystemExit(f"unknown runtime-budget lane: {args.lane}") from exc

    args.output_dir.mkdir(parents=True, exist_ok=True)
    junit_path = args.output_dir / f"{args.lane}.xml"
    json_path = args.output_dir / f"{args.lane}.json"
    markdown_path = args.output_dir / f"{args.lane}.md"

    command = [
        sys.executable,
        "-m",
        "pytest",
        *pytest_args,
        f"--junitxml={junit_path}",
        "--durations=25",
        "--durations-min=0.1",
    ]
    started = time.monotonic()
    completed = subprocess.run(command, cwd=ROOT, check=False)
    wall_seconds = time.monotonic() - started

    if not junit_path.exists():
        return completed.returncode or 2

    report = build_report(
        lane=args.lane,
        wall_seconds=wall_seconds,
        junit_path=junit_path,
        budget=budget,
        exit_code=completed.returncode,
    )
    violations = budget_violations(report)
    report["budget_violations"] = violations
    report["workflow_run_attempt"] = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))

    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = render_markdown(report, violations)
    markdown_path.write_text(markdown, encoding="utf-8")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(markdown)

    if completed.returncode:
        return completed.returncode
    if violations:
        for violation in violations:
            print(f"runtime budget violation: {violation}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
