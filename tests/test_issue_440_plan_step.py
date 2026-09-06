from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform.benchmarking.plan_step import (
    PlanStepBenchmarkHarness,
    PlanStepBenchmarkSpec,
)
from ai_multi_agent_platform.benchmarking.cli import main as benchmark_main


@pytest.mark.parametrize(
    ("scenario", "size", "expected_steps", "expected_width"),
    (
        ("linear", 4, 4, 1),
        ("fan-out", 3, 4, 3),
        ("fan-in", 3, 5, 3),
    ),
)
def test_plan_step_harness_preserves_durable_coordination_invariants(
    tmp_path: Path,
    scenario: str,
    size: int,
    expected_steps: int,
    expected_width: int,
) -> None:
    async def run() -> None:
        report = await PlanStepBenchmarkHarness(
            tmp_path / scenario,
            platform_commit="test-commit",
        ).run(
            PlanStepBenchmarkSpec(
                scenario=scenario,  # type: ignore[arg-type]
                size=size,
                timeout_seconds=5,
            )
        )
        assert report.correctness.passed is True
        assert report.correctness.expected_steps == expected_steps
        assert report.correctness.succeeded_steps == expected_steps
        assert report.correctness.run_created_events == expected_steps
        assert report.correctness.unique_run_ids == expected_steps
        assert report.correctness.dependency_order_valid is True
        assert report.correctness.task_succeeded is True
        assert report.correctness.active_width_peak == expected_width
        assert report.registration_latency.count == 1
        assert report.outcome_persistence_latency.count == expected_steps
        assert report.coordination_observation_latency.count == expected_steps
        assert len(report.step_ids) == expected_steps
        assert len(report.run_ids) == expected_steps
        assert len(set(report.run_ids)) == expected_steps
        assert report.resources.storage_growth_bytes > 0

    asyncio.run(run())


def test_plan_step_spec_enforces_safety_bounds() -> None:
    with pytest.raises(ValueError, match="size must be at least 1"):
        PlanStepBenchmarkSpec(scenario="linear", size=0)
    with pytest.raises(ValueError, match="safety_max_size must be at least 1"):
        PlanStepBenchmarkSpec(scenario="linear", size=1, safety_max_size=0)
    with pytest.raises(ValueError, match="size exceeds configured plan-step safety bound"):
        PlanStepBenchmarkSpec(scenario="fan-out", size=5, safety_max_size=4)
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        PlanStepBenchmarkSpec(scenario="fan-in", size=1, timeout_seconds=0)


def test_plan_step_cli_writes_machine_readable_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    data_dir = tmp_path / "data"
    exit_code = benchmark_main(
        [
            "plan-step-scale",
            "--scenario",
            "fan-in",
            "--size",
            "2",
            "--timeout-seconds",
            "5",
            "--safety-max-size",
            "8",
            "--data-dir",
            str(data_dir),
            "--platform-commit",
            "cli-test",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "1.0"
    assert report["benchmark"]["scenario"] == "fan-in"
    assert report["benchmark"]["size"] == 2
    assert report["benchmark"]["expected_step_count"] == 4
    assert report["correctness"]["passed"] is True
