from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.benchmarking.coordination_contention import (
    CoordinationContentionHarness,
    CoordinationContentionScenario,
    CoordinationContentionSpec,
)
from ai_multi_agent_platform.benchmarking.coordination_contention_cli import main as contention_main


@pytest.mark.parametrize("scenario", ("multi-plan", "claim-contention"))
def test_coordination_contention_preserves_invariants(tmp_path: Path, scenario: str) -> None:
    async def run() -> None:
        spec = CoordinationContentionSpec(
            scenario=cast(CoordinationContentionScenario, scenario),
            plan_count=2,
            steps_per_plan=2,
            claim_hold_seconds=0.01,
            timeout_seconds=5,
            safety_max_total_steps=8,
        )
        report = await CoordinationContentionHarness(
            tmp_path / scenario,
            platform_commit="test-commit",
        ).run(spec)
        assert report.correctness.passed is True
        assert report.correctness.expected_plans == 2
        assert report.correctness.succeeded_tasks == 2
        assert report.correctness.expected_steps == 4
        assert report.correctness.succeeded_steps == 4
        assert report.correctness.expected_runs == 4
        assert report.correctness.run_created_events == 4
        assert report.correctness.unique_run_ids == 4
        assert report.registration_latency.count == 2
        assert report.outcome_persistence_latency.count == 4
        assert report.completion_observation_latency.count == 4
        assert len(report.task_ids) == 2
        assert len(report.plan_ids) == 2
        assert len(report.step_ids) == 4
        assert len(report.run_ids) == 4
        assert report.resources.storage_bytes_after > 0
        assert (
            report.resources.storage_growth_bytes
            == report.resources.storage_bytes_after - report.resources.storage_bytes_before
        )
        if scenario == "claim-contention":
            assert report.blocked_observation_latency.count == 4
            assert report.correctness.blocked_observations == 4
            assert report.correctness.recovered_observations == 4
            assert report.correctness.stale_claim_rejections == 4
            assert report.correctness.fence_advanced_steps == 4
        else:
            assert report.blocked_observation_latency.count == 0
            assert report.correctness.blocked_observations == 0
            assert report.correctness.recovered_observations == 0
            assert report.correctness.stale_claim_rejections == 0
            assert report.correctness.fence_advanced_steps == 0

    asyncio.run(run())


def test_coordination_contention_spec_enforces_bounds() -> None:
    with pytest.raises(ValueError, match="plan_count must be at least 1"):
        CoordinationContentionSpec(scenario="multi-plan", plan_count=0, steps_per_plan=1)
    with pytest.raises(ValueError, match="steps_per_plan must be at least 1"):
        CoordinationContentionSpec(scenario="multi-plan", plan_count=1, steps_per_plan=0)
    with pytest.raises(ValueError, match="safety_max_total_steps must be at least 1"):
        CoordinationContentionSpec(
            scenario="claim-contention",
            plan_count=1,
            steps_per_plan=1,
            safety_max_total_steps=0,
        )
    with pytest.raises(ValueError, match="exceeds configured coordination contention"):
        CoordinationContentionSpec(
            scenario="multi-plan",
            plan_count=3,
            steps_per_plan=2,
            safety_max_total_steps=5,
        )
    with pytest.raises(ValueError, match="claim_hold_seconds must be positive"):
        CoordinationContentionSpec(
            scenario="claim-contention",
            plan_count=1,
            steps_per_plan=1,
            claim_hold_seconds=0,
        )
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        CoordinationContentionSpec(
            scenario="multi-plan",
            plan_count=1,
            steps_per_plan=1,
            timeout_seconds=0,
        )
    with pytest.raises(ValueError, match="requires exactly two coordinators"):
        CoordinationContentionSpec(
            scenario="multi-plan",
            plan_count=1,
            steps_per_plan=1,
            coordinator_count=3,
        )


def test_coordination_contention_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    exit_code = contention_main(
        [
            "--scenario",
            "claim-contention",
            "--plan-count",
            "2",
            "--steps-per-plan",
            "2",
            "--claim-hold-seconds",
            "0.01",
            "--timeout-seconds",
            "5",
            "--safety-max-total-steps",
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
    assert report["benchmark"]["scenario"] == "claim-contention"
    assert report["benchmark"]["plan_count"] == 2
    assert report["benchmark"]["steps_per_plan"] == 2
    assert report["benchmark"]["total_steps"] == 4
    assert report["correctness"]["blocked_observations"] == 4
    assert report["correctness"]["recovered_observations"] == 4
    assert report["correctness"]["passed"] is True
