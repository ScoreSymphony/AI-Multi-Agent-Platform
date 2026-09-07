from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.host_pressure import (
    HostPressureBenchmarkSpec,
    HostPressureSemanticBenchmarkHarness,
)


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-host-pressure.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def test_host_pressure_semantic_fixture_is_bounded_correct_and_schema_valid() -> None:
    spec = HostPressureBenchmarkSpec(
        benchmark_id="host-pressure.semantic.admission",
        benchmark_version="1.0",
        iterations_per_phase=3,
        safety_max_iterations_per_phase=4,
    )
    report = HostPressureSemanticBenchmarkHarness(platform_commit="pressure-test-sha").run(spec)

    assert report.platform_commit == "pressure-test-sha"
    assert report.correctness.passed is True
    assert report.correctness.attempted_decisions == 12
    assert report.correctness.unexpected_decisions == 0
    assert report.correctness.scheduler_acceptance_mismatches == 0
    assert report.correctness.active_reservations_after == 0
    assert report.correctness.recovered is True
    assert report.errors == ()

    assert report.phase_action_counts["healthy"] == {"admit": 3}
    assert report.phase_action_counts["elevated"] == {"queue": 3}
    assert report.phase_action_counts["critical"] == {"deny_temporarily": 3}
    assert report.phase_action_counts["recovery"] == {"admit": 3}
    assert report.phase_reason_counts["elevated"] == {"pressure_elevated": 3}
    assert report.phase_reason_counts["critical"] == {"pressure_critical": 3}
    assert report.phase_scheduler_acceptance_counts["healthy"] == {"accepted": 3}
    assert report.phase_scheduler_acceptance_counts["elevated"] == {"rejected": 3}
    assert report.phase_scheduler_acceptance_counts["critical"] == {"rejected": 3}
    assert report.phase_scheduler_acceptance_counts["recovery"] == {"accepted": 3}

    for phase in ("healthy", "elevated", "critical", "recovery"):
        assert report.phase_latency[phase].count == 3

    assert report.safety.synthetic_fixture is True
    assert report.safety.host_mutation_attempted is False
    assert report.safety.destructive_load_generated is False
    Draft202012Validator(_schema()).validate(report.to_dict())


def test_host_pressure_semantic_fixture_enforces_hard_iteration_bound() -> None:
    with pytest.raises(ValueError, match="safety limit"):
        HostPressureBenchmarkSpec(
            benchmark_id="host-pressure.semantic.admission",
            benchmark_version="1.0",
            iterations_per_phase=5,
            safety_max_iterations_per_phase=4,
        )


def test_host_pressure_semantic_fixture_rejects_non_heavy_workload_class() -> None:
    with pytest.raises(ValueError, match="workload_class"):
        HostPressureBenchmarkSpec(
            benchmark_id="host-pressure.semantic.admission",
            benchmark_version="1.0",
            iterations_per_phase=1,
            workload_class="light",
        )
