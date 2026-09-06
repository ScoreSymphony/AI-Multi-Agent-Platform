from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.benchmarking.coordination_pressure import (
    CoordinationPressureHarness,
    CoordinationPressureScenario,
    CoordinationPressureSpec,
)
from ai_multi_agent_platform.benchmarking.coordination_pressure_cli import main as pressure_main


@pytest.mark.parametrize(
    ("scenario", "expected_runs", "expected_attempt"),
    (
        ("retry-burst", 6, 2),
        ("deadline-wait-burst", 3, 1),
        ("restart-reconcile", 3, 1),
    ),
)
def test_coordination_pressure_preserves_scenario_invariants(
    tmp_path: Path,
    scenario: str,
    expected_runs: int,
    expected_attempt: int,
) -> None:
    async def run() -> None:
        report = await CoordinationPressureHarness(
            tmp_path / scenario,
            platform_commit="test-commit",
        ).run(
            CoordinationPressureSpec(
                scenario=cast(CoordinationPressureScenario, scenario),
                size=3,
                retry_delay_seconds=0.01,
                wait_delay_seconds=0.01,
                timeout_seconds=5,
            )
        )
        assert report.correctness.passed is True
        assert report.correctness.expected_steps == 3
        assert report.correctness.succeeded_steps == 3
        assert report.correctness.expected_runs == expected_runs
        assert report.correctness.run_created_events == expected_runs
        assert report.correctness.unique_run_ids == expected_runs
        assert report.correctness.maximum_attempt == expected_attempt
        assert report.correctness.task_succeeded is True
        assert report.resume_or_reconcile_latency.count == 1
        assert report.outcome_persistence_latency.count >= 3
        assert report.coordination_observation_latency.count >= 3
        assert len(report.run_ids) == 3
        assert report.resources.storage_bytes_after > 0
        assert (
            report.resources.storage_growth_bytes
            == report.resources.storage_bytes_after - report.resources.storage_bytes_before
        )
        if scenario == "retry-burst":
            assert report.correctness.retry_scheduled_steps == 3
            assert report.transition_latency.count == 3
        elif scenario == "deadline-wait-burst":
            assert report.correctness.wait_entered_steps == 3
            assert report.correctness.wait_resolved_steps == 3
            assert report.transition_latency.count == 3
        else:
            assert report.correctness.reconciled_running_steps == 3
            assert report.correctness.run_identity_preserved is True
            assert report.transition_latency.count == 0

    asyncio.run(run())


def test_coordination_pressure_spec_enforces_safety_bounds() -> None:
    with pytest.raises(ValueError, match="size must be at least 1"):
        CoordinationPressureSpec(scenario="retry-burst", size=0)
    with pytest.raises(ValueError, match="safety_max_size must be at least 1"):
        CoordinationPressureSpec(scenario="deadline-wait-burst", size=1, safety_max_size=0)
    with pytest.raises(ValueError, match="exceeds configured coordination pressure"):
        CoordinationPressureSpec(scenario="restart-reconcile", size=5, safety_max_size=4)
    with pytest.raises(ValueError, match="retry_delay_seconds must not be negative"):
        CoordinationPressureSpec(scenario="retry-burst", size=1, retry_delay_seconds=-1)
    with pytest.raises(ValueError, match="wait_delay_seconds must not be negative"):
        CoordinationPressureSpec(scenario="deadline-wait-burst", size=1, wait_delay_seconds=-1)
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        CoordinationPressureSpec(scenario="restart-reconcile", size=1, timeout_seconds=0)


def test_coordination_pressure_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    exit_code = pressure_main(
        [
            "--scenario",
            "retry-burst",
            "--size",
            "2",
            "--retry-delay-seconds",
            "0.01",
            "--timeout-seconds",
            "5",
            "--safety-max-size",
            "8",
            "--data-dir",
            str(tmp_path / "data"),
            "--platform-commit",
            "cli-test",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["benchmark"]["scenario"] == "retry-burst"
    assert report["benchmark"]["size"] == 2
    assert report["benchmark"]["expected_runs"] == 4
    assert report["correctness"]["passed"] is True
