from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.run_pytest_lane import budget_violations, build_report  # noqa: E402
from scripts.ci.verify_pytest_runtime_aggregate import (  # noqa: E402
    aggregate_violations,
    build_aggregate,
)
from scripts.ci.verify_pytest_shards import compare_collections  # noqa: E402


def test_runtime_report_aggregates_test_and_module_durations(tmp_path: Path) -> None:
    junit = tmp_path / "lane.xml"
    junit.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3" failures="0" errors="0" skipped="1" time="3.5">
    <testcase classname="tests.integration.alpha" name="test_fast" time="0.5" />
    <testcase classname="tests.integration.alpha" name="test_slow" time="2.0" />
    <testcase classname="tests.integration.beta" name="test_skip" time="1.0">
      <skipped />
    </testcase>
  </testsuite>
</testsuites>
""",
        encoding="utf-8",
    )

    report = build_report(
        lane="integration",
        wall_seconds=4.0,
        junit_path=junit,
        budget={
            "wall_seconds": 10,
            "slow_test_seconds": 5,
            "slow_module_seconds": 5,
        },
        exit_code=0,
    )

    assert report["testcase_count"] == 3
    assert report["skipped_count"] == 1
    assert report["testcase_seconds_sum"] == 3.5
    assert report["slowest_tests"][0]["node"].endswith("::test_slow")
    assert report["slowest_modules"][0] == {
        "module": "tests.integration.alpha",
        "seconds": 2.5,
    }
    assert report["slowest_directories"][0] == {
        "directory": "tests/integration",
        "seconds": 3.5,
    }
    assert budget_violations(report) == []


def test_runtime_budget_detects_lane_test_and_module_regressions(tmp_path: Path) -> None:
    junit = tmp_path / "lane.xml"
    junit.write_text(
        """<testsuite>
  <testcase classname="tests.integration.alpha" name="test_slow" time="7.0" />
</testsuite>""",
        encoding="utf-8",
    )
    report = build_report(
        lane="integration",
        wall_seconds=12.0,
        junit_path=junit,
        budget={
            "wall_seconds": 10,
            "slow_test_seconds": 5,
            "slow_module_seconds": 6,
        },
        exit_code=0,
    )

    violations = budget_violations(report)

    assert len(violations) == 3
    assert "wall time" in violations[0]
    assert "slowest test" in violations[1]
    assert "slowest module" in violations[2]


def test_shard_collection_comparison_detects_missing_unexpected_and_duplicates() -> None:
    baseline = {"a::test_one", "b::test_two", "c::test_three"}
    shards = {
        "one": {"a::test_one", "b::test_two"},
        "two": {"b::test_two", "extra::test_four"},
    }

    missing, unexpected, duplicates = compare_collections(baseline, shards)

    assert missing == {"c::test_three"}
    assert unexpected == {"extra::test_four"}
    assert duplicates == {"b::test_two"}


def test_shard_collection_comparison_accepts_exact_partition() -> None:
    baseline = {"a::test_one", "b::test_two", "c::test_three"}
    shards = {
        "one": {"a::test_one"},
        "two": {"b::test_two", "c::test_three"},
    }

    assert compare_collections(baseline, shards) == (set(), set(), set())


def test_runtime_aggregate_uses_slowest_lane_and_enforces_budget() -> None:
    reports = {
        "unit": {"wall_seconds": 15.0, "pytest_exit_code": 0, "budget_violations": []},
        "contract-architecture-release": {
            "wall_seconds": 40.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
        "integration": {
            "wall_seconds": 122.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
        "system-regression": {
            "wall_seconds": 96.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
    }
    validation_reports = {
        "quality": {"wall_seconds": 70.0},
        "unit": {"wall_seconds": 83.0},
        "contract-architecture-release": {"wall_seconds": 43.0},
        "integration": {"wall_seconds": 148.0},
        "system-regression": {"wall_seconds": 121.0},
    }

    summary = build_aggregate(reports, validation_reports)

    assert summary["pytest_critical_lane"] == "integration"
    assert summary["pytest_critical_path_seconds"] == 122.0
    assert summary["pytest_total_compute_seconds"] == 273.0
    assert summary["validation_critical_lane"] == "integration"
    assert summary["validation_critical_path_seconds"] == 148.0
    assert (
        aggregate_violations(
            summary,
            {
                "pytest_critical_path_seconds": 330,
                "validation_critical_path_seconds": 350,
            },
        )
        == []
    )
    assert aggregate_violations(
        summary,
        {
            "pytest_critical_path_seconds": 100,
            "validation_critical_path_seconds": 140,
        },
    ) == [
        "pytest critical path 122.000s exceeds 100.000s",
        "validation critical path 148.000s exceeds 140.000s",
    ]


def test_runtime_aggregate_rejects_failed_or_over_budget_lane() -> None:
    reports = {
        "unit": {"wall_seconds": 15.0, "pytest_exit_code": 0, "budget_violations": []},
        "contract-architecture-release": {
            "wall_seconds": 40.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
        "integration": {
            "wall_seconds": 122.0,
            "pytest_exit_code": 0,
            "budget_violations": ["slowest test exceeded budget"],
        },
        "system-regression": {
            "wall_seconds": 96.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
    }
    validation_reports = {
        "quality": {"wall_seconds": 70.0},
        "unit": {"wall_seconds": 83.0},
        "contract-architecture-release": {"wall_seconds": 43.0},
        "integration": {"wall_seconds": 148.0},
        "system-regression": {"wall_seconds": 121.0},
    }

    summary = build_aggregate(reports, validation_reports)

    assert aggregate_violations(
        summary,
        {
            "pytest_critical_path_seconds": 330,
            "validation_critical_path_seconds": 350,
        },
    ) == ["failed or over-budget lanes: integration"]


def test_ci_runtime_artifacts_are_scoped_to_current_workflow_attempt() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "name: python-test-runtime-${{ matrix.lane }}-attempt-${{ github.run_attempt }}" in workflow
    assert "name: python-validation-runtime-${{ matrix.lane }}-attempt-${{ github.run_attempt }}" in workflow
    assert "pattern: python-test-runtime-*-attempt-${{ github.run_attempt }}" in workflow
    assert "pattern: python-validation-runtime-*-attempt-${{ github.run_attempt }}" in workflow
    assert "name: python-test-runtime-aggregate-attempt-${{ github.run_attempt }}" in workflow
