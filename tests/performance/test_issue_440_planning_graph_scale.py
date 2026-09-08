from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.planning_graph_scale import (
    PLANNING_GRAPH_SCALE_REPORT_SCHEMA_VERSION,
    PlanningGraphScaleBenchmarkHarness,
    PlanningGraphScaleBenchmarkSpec,
)
from ai_multi_agent_platform.benchmarking.planning_graph_scale_cli import main


def _schema() -> dict[str, object]:
    return json.loads(
        Path("docs/schemas/benchmark-planning-graph-scale.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.performance
def test_planning_graph_scale_measures_validation_activation_and_inspection(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        report = await PlanningGraphScaleBenchmarkHarness(
            tmp_path / "planning-graph-scale",
            platform_commit="test-commit",
        ).run(
            PlanningGraphScaleBenchmarkSpec(
                step_counts=(1, 4),
                repetitions=2,
                warmup_repetitions=1,
                timeout_seconds=5.0,
                safety_max_steps_per_plan=8,
                safety_max_repetitions=4,
            )
        )

        payload = report.to_dict()
        Draft202012Validator(_schema()).validate(payload)
        assert report.schema_version == PLANNING_GRAPH_SCALE_REPORT_SCHEMA_VERSION
        assert report.correctness.passed is True
        assert report.correctness.requested_points == 2
        assert report.correctness.completed_points == 2
        assert report.correctness.requested_repetitions == 4
        assert report.correctness.completed_repetitions == 4
        assert report.correctness.provider_invocations == 0
        assert report.errors == ()
        assert report.resources.storage_growth_bytes > 0

        for point, step_count in zip(report.points, (1, 4), strict=True):
            assert point.step_count == step_count
            assert point.passed is True
            assert point.completed_repetitions == 2
            assert point.validated_proposals == 2
            assert point.activated_plans == 2
            assert point.canonical_handoffs == 2
            assert point.exact_step_inspections == 2
            assert point.provider_invocations == 0
            assert point.proposal_validation_latency.count == 2
            assert point.activation_handoff_latency.count == 2
            assert point.inspection_latency.count == 2
            assert point.errors == ()

    asyncio.run(scenario())


@pytest.mark.performance
def test_planning_graph_scale_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    output = tmp_path / "report.json"
    exit_code = main(
        [
            "--step-counts",
            "1,3",
            "--repetitions",
            "1",
            "--timeout-seconds",
            "5",
            "--safety-max-steps-per-plan",
            "4",
            "--safety-max-repetitions",
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
    assert payload["benchmark"]["benchmark_id"] == "single-node.planning-graph-scale"
    assert payload["benchmark"]["step_counts"] == [1, 3]
    assert [point["step_count"] for point in payload["points"]] == [1, 3]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"step_counts": ()}, "step_counts must contain at least one graph size"),
        ({"step_counts": (0,)}, "step_counts must contain only positive integers"),
        ({"step_counts": (2, 2)}, "step_counts must not contain duplicates"),
        ({"step_counts": (2, 1)}, "step_counts must be strictly increasing"),
        ({"repetitions": 0}, "repetitions must be at least 1"),
        ({"warmup_repetitions": -1}, "warmup_repetitions must not be negative"),
        ({"timeout_seconds": 0.0}, "timeout_seconds must be positive"),
        (
            {"step_counts": (1, 4), "safety_max_steps_per_plan": 3},
            "step_counts exceed configured planning-graph safety bound",
        ),
        (
            {"repetitions": 3, "safety_max_repetitions": 2},
            "repetitions exceed configured planning-graph safety bound",
        ),
        (
            {"warmup_repetitions": 3, "safety_max_repetitions": 2},
            "warmup_repetitions exceed configured planning-graph safety bound",
        ),
    ],
)
def test_planning_graph_scale_spec_enforces_safety_bounds(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        PlanningGraphScaleBenchmarkSpec(**kwargs)
