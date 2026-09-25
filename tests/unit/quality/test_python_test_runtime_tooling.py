from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.run_pytest_lane import budget_violations, build_report  # noqa: E402
from scripts.ci.verify_pytest_runtime_aggregate import (  # noqa: E402
    aggregate_violations,
    build_aggregate,
    load_lane_reports,
    load_validation_reports,
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
    assert "lane default" in violations[1]
    assert "slowest module" in violations[2]


def test_runtime_budget_accepts_targeted_slow_test_override(tmp_path: Path) -> None:
    junit = tmp_path / "lane.xml"
    junit.write_text(
        """<testsuite>
  <testcase classname="tests.performance.alpha" name="test_fixture_heavy" time="18.0" />
  <testcase classname="tests.performance.alpha" name="test_regular" time="14.0" />
</testsuite>""",
        encoding="utf-8",
    )
    node = "tests.performance.alpha::test_fixture_heavy"
    report = build_report(
        lane="system-regression",
        wall_seconds=40.0,
        junit_path=junit,
        budget={
            "wall_seconds": 240,
            "slow_test_seconds": 15,
            "slow_test_overrides": {node: 20},
            "slow_module_seconds": 40,
        },
        exit_code=0,
    )

    assert budget_violations(report) == []


def test_runtime_budget_override_does_not_mask_other_slow_test(tmp_path: Path) -> None:
    junit = tmp_path / "lane.xml"
    junit.write_text(
        """<testsuite>
  <testcase classname="tests.performance.alpha" name="test_fixture_heavy" time="18.0" />
  <testcase classname="tests.performance.beta" name="test_unexpectedly_slow" time="16.0" />
</testsuite>""",
        encoding="utf-8",
    )
    node = "tests.performance.alpha::test_fixture_heavy"
    report = build_report(
        lane="system-regression",
        wall_seconds=40.0,
        junit_path=junit,
        budget={
            "wall_seconds": 240,
            "slow_test_seconds": 15,
            "slow_test_overrides": {node: 20},
            "slow_module_seconds": 40,
        },
        exit_code=0,
    )

    violations = budget_violations(report)

    assert len(violations) == 1
    assert "tests.performance.beta::test_unexpectedly_slow" in violations[0]
    assert "budget 15.000s, lane default" in violations[0]


def test_runtime_budget_rejects_invalid_slow_test_overrides_shape(tmp_path: Path) -> None:
    junit = tmp_path / "lane.xml"
    junit.write_text(
        """<testsuite>
  <testcase classname="tests.performance.alpha" name="test_fast" time="1.0" />
</testsuite>""",
        encoding="utf-8",
    )
    report = build_report(
        lane="system-regression",
        wall_seconds=2.0,
        junit_path=junit,
        budget={
            "wall_seconds": 240,
            "slow_test_seconds": 15,
            "slow_test_overrides": [],
            "slow_module_seconds": 40,
        },
        exit_code=0,
    )

    with pytest.raises(ValueError, match="slow_test_overrides must be an object"):
        budget_violations(report)


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
        "integration-core": {
            "wall_seconds": 122.0,
            "pytest_exit_code": 0,
            "budget_violations": [],
        },
        "integration-recovery-deployment": {
            "wall_seconds": 95.0,
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
        "integration-core": {"wall_seconds": 148.0},
        "integration-recovery-deployment": {"wall_seconds": 132.0},
        "system-regression": {"wall_seconds": 121.0},
    }

    summary = build_aggregate(reports, validation_reports)

    assert summary["pytest_critical_lane"] == "integration-core"
    assert summary["pytest_critical_path_seconds"] == 122.0
    assert summary["pytest_total_compute_seconds"] == 368.0
    assert summary["validation_critical_lane"] == "integration-core"
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
        "integration-core": {
            "wall_seconds": 122.0,
            "pytest_exit_code": 0,
            "budget_violations": ["slowest test exceeded budget"],
        },
        "integration-recovery-deployment": {
            "wall_seconds": 95.0,
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
        "integration-core": {"wall_seconds": 148.0},
        "integration-recovery-deployment": {"wall_seconds": 132.0},
        "system-regression": {"wall_seconds": 121.0},
    }

    summary = build_aggregate(reports, validation_reports)

    assert aggregate_violations(
        summary,
        {
            "pytest_critical_path_seconds": 330,
            "validation_critical_path_seconds": 350,
        },
    ) == ["failed or over-budget lanes: integration-core"]


def test_runtime_report_loaders_prefer_latest_rerun_attempt(tmp_path: Path) -> None:
    pytest_reports = tmp_path / "pytest"
    validation_reports = tmp_path / "validation"
    pytest_reports.mkdir()
    validation_reports.mkdir()

    (pytest_reports / "system-attempt-1.json").write_text(
        json.dumps(
            {
                "lane": "system-regression",
                "run_attempt": 1,
                "wall_seconds": 196.418,
                "pytest_exit_code": 0,
                "budget_violations": ["slowest test exceeded budget"],
            }
        ),
        encoding="utf-8",
    )
    (pytest_reports / "system-attempt-2.json").write_text(
        json.dumps(
            {
                "lane": "system-regression",
                "run_attempt": 2,
                "wall_seconds": 91.0,
                "pytest_exit_code": 0,
                "budget_violations": [],
            }
        ),
        encoding="utf-8",
    )
    (validation_reports / "system-attempt-1.json").write_text(
        json.dumps(
            {
                "lane": "system-regression",
                "run_attempt": 1,
                "wall_seconds": 223.701,
            }
        ),
        encoding="utf-8",
    )
    (validation_reports / "system-attempt-2.json").write_text(
        json.dumps(
            {
                "lane": "system-regression",
                "run_attempt": 2,
                "wall_seconds": 117.0,
            }
        ),
        encoding="utf-8",
    )

    selected_pytest = load_lane_reports(pytest_reports)
    selected_validation = load_validation_reports(validation_reports)

    assert selected_pytest["system-regression"]["run_attempt"] == 2
    assert selected_pytest["system-regression"]["budget_violations"] == []
    assert selected_validation["system-regression"]["run_attempt"] == 2
    assert selected_validation["system-regression"]["wall_seconds"] == 117.0


def test_runtime_report_loader_rejects_duplicate_same_attempt(tmp_path: Path) -> None:
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "lane": "integration-core",
                    "run_attempt": 2,
                    "wall_seconds": 1.0,
                    "pytest_exit_code": 0,
                    "budget_violations": [],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(
        ValueError,
        match="duplicate pytest runtime report for lane integration-core at run attempt 2",
    ):
        load_lane_reports(tmp_path)


def test_runtime_report_loader_rejects_duplicate_stale_attempt(
    tmp_path: Path,
) -> None:
    reports = [
        ("a.json", 1),
        ("b.json", 2),
        ("c.json", 1),
    ]
    for name, attempt in reports:
        (tmp_path / name).write_text(
            json.dumps(
                {
                    "lane": "integration-core",
                    "run_attempt": attempt,
                    "wall_seconds": 1.0,
                    "pytest_exit_code": 0,
                    "budget_violations": [],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(
        ValueError,
        match="duplicate pytest runtime report for lane integration-core at run attempt 1",
    ):
        load_lane_reports(tmp_path)
