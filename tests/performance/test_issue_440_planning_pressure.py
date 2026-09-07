from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.planning_pressure import (
    PLANNING_PRESSURE_REPORT_SCHEMA_VERSION,
    PlanningPressureBenchmarkHarness,
    PlanningPressureBenchmarkSpec,
)
from ai_multi_agent_platform.benchmarking.planning_pressure_cli import main


def _schema() -> dict[str, object]:
    return json.loads(
        Path("docs/schemas/benchmark-planning-pressure.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.performance
def test_planning_pressure_measures_canonical_replanning_without_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        report = await PlanningPressureBenchmarkHarness(
            tmp_path / "planning-pressure",
            platform_commit="test-commit",
        ).run(
            PlanningPressureBenchmarkSpec(
                operation_count=2,
                concurrency=2,
                steps_per_plan=2,
                warmup_operations=1,
                timeout_seconds=5.0,
                safety_max_operations=4,
                safety_max_concurrency=4,
                safety_max_steps_per_plan=4,
            )
        )

        payload = report.to_dict()
        Draft202012Validator(_schema()).validate(payload)
        assert report.schema_version == PLANNING_PRESSURE_REPORT_SCHEMA_VERSION
        assert report.correctness.passed is True
        assert report.correctness.requested_cycles == 2
        assert report.correctness.completed_cycles == 2
        assert report.correctness.initial_proposals_validated == 2
        assert report.correctness.initial_plans_activated == 2
        assert report.correctness.terminal_replans_validated == 2
        assert report.correctness.replacement_plans_activated == 2
        assert report.correctness.canonical_failure_evidence_resolved == 2
        assert report.correctness.distinct_replacement_plans == 2
        assert report.correctness.provider_invocations == 0
        assert report.initial_proposal_latency.count == 2
        assert report.initial_activation_latency.count == 2
        assert report.replan_proposal_latency.count == 2
        assert report.replan_activation_latency.count == 2
        assert report.resources.storage_growth_bytes > 0
        assert report.errors == ()
        assert report.benchmark.planner_profile == "deterministic-reference-no-model-provider"

    asyncio.run(scenario())


@pytest.mark.performance
def test_planning_pressure_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    exit_code = main(
        [
            "--operations",
            "1",
            "--concurrency",
            "1",
            "--steps-per-plan",
            "1",
            "--timeout-seconds",
            "5",
            "--safety-max-operations",
            "2",
            "--safety-max-concurrency",
            "2",
            "--safety-max-steps-per-plan",
            "2",
            "--data-dir",
            str(tmp_path / "data"),
            "--platform-commit",
            "cli-test",
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    Draft202012Validator(_schema()).validate(payload)
    assert payload["platform_commit"] == "cli-test"
    assert payload["correctness"]["passed"] is True
    assert payload["correctness"]["provider_invocations"] == 0
    assert payload["benchmark"]["benchmark_id"] == "single-node.planning-pressure"


def test_planning_pressure_cli_bounds_warmup_work(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    with pytest.raises(
        ValueError,
        match="warmup_operations exceeds configured planning-pressure safety bound",
    ):
        main(
            [
                "--operations",
                "1",
                "--concurrency",
                "1",
                "--warmup-operations",
                "3",
                "--safety-max-operations",
                "2",
                "--output",
                str(output),
            ]
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"operation_count": 0, "concurrency": 1}, "operation_count must be at least 1"),
        ({"operation_count": 1, "concurrency": 0}, "concurrency must be at least 1"),
        (
            {"operation_count": 2, "concurrency": 1, "safety_max_operations": 1},
            "operation_count exceeds configured planning-pressure safety bound",
        ),
        (
            {"operation_count": 1, "concurrency": 2, "safety_max_concurrency": 1},
            "concurrency exceeds configured planning-pressure safety bound",
        ),
        (
            {
                "operation_count": 1,
                "concurrency": 1,
                "steps_per_plan": 2,
                "safety_max_steps_per_plan": 1,
            },
            "steps_per_plan exceeds configured planning-pressure safety bound",
        ),
    ],
)
def test_planning_pressure_spec_enforces_safety_bounds(
    kwargs: dict[str, int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PlanningPressureBenchmarkSpec(**kwargs)
