from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.operating_envelope import OperatingEnvelopeAnalyzer
from ai_multi_agent_platform.benchmarking.operating_envelope_cli import main

_ENVIRONMENT = {
    "system": "Linux",
    "release": "test-release",
    "machine": "x86_64",
    "processor": "test-cpu",
    "python_implementation": "CPython",
    "python_version": "3.12.0",
    "python_major_minor": "3.12",
}


def _sweep_report(
    *,
    commit: str = "release-sha",
    environment: dict[str, object] | None = None,
    throughput_offset: float = 0.0,
    correctness_passed: bool = True,
) -> dict[str, object]:
    points: list[dict[str, object]] = []
    for concurrency, base_throughput, base_p95 in ((1, 100.0, 4.0), (10, 700.0, 12.0)):
        for repetition in (1, 2):
            points.append(
                {
                    "concurrency": concurrency,
                    "repetition": repetition,
                    "report_file": f"c-{concurrency}-r-{repetition}.json",
                    "throughput_operations_per_second": (
                        base_throughput + throughput_offset + repetition
                    ),
                    "p95_latency_ms": base_p95 + repetition,
                    "duration_seconds": 2.0 + repetition,
                    "completed_operations": 100,
                    "storage_growth_bytes": 1000 * repetition,
                    "correctness_passed": correctness_passed,
                }
            )
    return {
        "schema_version": "1.0",
        "benchmark_id": "single-node.reference.lifecycle.sweep",
        "benchmark_version": "1.0",
        "platform_version": "0.0.1",
        "platform_commit": commit,
        "started_at": "2026-09-07T20:00:00+00:00",
        "deployment_profile": "single-node-reference",
        "persistence_profile": "sqlite-reference",
        "workload_distribution": "deterministic-task-lifecycle",
        "operation_count_per_point": 100,
        "warmup_operations": 5,
        "timeout_seconds": 30.0,
        "repetitions": 2,
        "concurrency_levels": [1, 10],
        "environment": dict(environment or _ENVIRONMENT),
        "points": points,
        "correctness_passed": correctness_passed,
        "errors": [],
    }


def _endurance_report(
    *,
    commit: str = "release-sha",
    environment: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "benchmark": {
            "benchmark_id": "single-node.reference.endurance",
            "benchmark_version": "1.0",
            "scenario": "soak",
            "deployment_profile": "single-node-reference",
            "persistence_profile": "sqlite-reference",
            "duration_seconds": 3600.0,
            "sample_interval_seconds": 10.0,
            "max_operations": 20000,
            "concurrency": 10,
            "seed_tasks": 100,
            "warmup_operations": 5,
            "timeout_seconds": 30.0,
            "read_weight": 4,
            "write_weight": 1,
            "repetition_count": 1,
            "optional_subsystems": [],
            "expected_invariants": ["canonical-state-correctness"],
            "captured_metrics": ["latency-drift"],
        },
        "platform_version": "0.0.1",
        "platform_commit": commit,
        "started_at": "2026-09-07T20:00:00+00:00",
        "duration_seconds": 3600.0,
        "environment": dict(environment or _ENVIRONMENT),
        "startup_latency": _latency(1.0),
        "throughput_operations_per_second": 50.0,
        "operation_latency": _latency(15.0),
        "read_latency": _latency(10.0),
        "write_latency": _latency(20.0),
        "snapshots": [
            _snapshot(0.0, 0, 0),
            _snapshot(3600.0, 20000, 20000),
        ],
        "resources": {
            "process_cpu_seconds": 120.0,
            "traced_memory_current_bytes": 1500000,
            "traced_memory_peak_bytes": 2000000,
            "peak_rss_bytes": 50000000,
            "storage_bytes_before": 1000,
            "storage_bytes_after": 5000,
            "storage_growth_bytes": 4000,
            "open_file_descriptors": 20,
        },
        "correctness": {
            "attempted_operations": 20000,
            "completed_operations": 20000,
            "failed_operations": 0,
            "seeded_tasks": 100,
            "observed_tasks": 4100,
            "observed_runs": 4100,
            "duplicate_write_task_ids": 0,
            "duplicate_write_run_ids": 0,
            "passed": True,
        },
        "measurements": {
            "stop_reason": "duration",
            "resource_snapshot_count": 2,
            "traced_memory_growth_bytes": 250000,
            "peak_rss_growth_bytes": 1000000,
            "open_file_descriptor_growth": 1,
            "storage_growth_bytes": 4000,
            "latency_drift_ratio": 0.04,
        },
        "errors": [],
    }


def _latency(p95: float) -> dict[str, object]:
    return {
        "count": 10,
        "min_ms": 1.0,
        "mean_ms": p95 / 2,
        "p50_ms": p95 / 2,
        "p95_ms": p95,
        "p99_ms": p95,
        "max_ms": p95,
    }


def _snapshot(
    elapsed_seconds: float,
    attempted_operations: int,
    completed_operations: int,
) -> dict[str, object]:
    return {
        "elapsed_seconds": elapsed_seconds,
        "process_cpu_seconds": elapsed_seconds / 10,
        "traced_memory_current_bytes": 1000000,
        "traced_memory_peak_bytes": 1500000,
        "peak_rss_bytes": 50000000,
        "storage_bytes": 1000,
        "open_file_descriptors": 20,
        "attempted_operations": attempted_operations,
        "completed_operations": completed_operations,
        "failed_operations": 0,
        "read_operations": completed_operations,
        "write_operations": 0,
        "window_operation_latency": _latency(10.0),
    }


def test_operating_envelope_aggregates_comparable_sweep_and_soak_evidence() -> None:
    report = OperatingEnvelopeAnalyzer().analyze(
        sweep_reports=(
            _sweep_report(throughput_offset=0.0),
            _sweep_report(throughput_offset=10.0),
        ),
        endurance_reports=(_endurance_report(),),
        sweep_sources=("sweep-a.json", "sweep-b.json"),
        endurance_sources=("soak.json",),
    )

    assert report.correctness_passed is True
    assert report.highest_verified_concurrency == 10
    assert report.longest_verified_endurance_seconds == 3600.0
    assert report.claim_semantics == "tested-envelope-only"
    assert report.budget_status == "not-established"
    assert len(report.environment_fingerprint_sha256) == 64

    by_concurrency = {point.concurrency: point for point in report.concurrency_envelope}
    assert by_concurrency[1].sample_count == 4
    assert by_concurrency[1].throughput_min_operations_per_second == 101.0
    assert by_concurrency[1].throughput_median_operations_per_second == 106.5
    assert by_concurrency[10].throughput_max_operations_per_second == 712.0
    assert by_concurrency[10].correctness_passed is True

    endurance = report.endurance_evidence[0]
    assert endurance.source == "soak.json"
    assert endurance.completed_operations == 20000
    assert endurance.traced_memory_growth_bytes == 250000
    assert endurance.latency_drift_ratio == 0.04

    schema = json.loads(
        Path("docs/schemas/benchmark-operating-envelope.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(report.to_dict())


@pytest.mark.parametrize(
    ("candidate", "match"),
    [
        (_sweep_report(commit="different-sha"), "platform_commit"),
        (
            _sweep_report(environment={**_ENVIRONMENT, "machine": "different"}),
            "environment",
        ),
        (_sweep_report(correctness_passed=False), "correctness"),
    ],
)
def test_operating_envelope_rejects_incomparable_or_failed_sweeps(
    candidate: dict[str, object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        OperatingEnvelopeAnalyzer().analyze(
            sweep_reports=(_sweep_report(), candidate),
        )


def test_operating_envelope_rejects_incomplete_repetitions() -> None:
    candidate = _sweep_report()
    points = candidate["points"]
    assert isinstance(points, list)
    candidate["points"] = points[:-1]

    with pytest.raises(ValueError, match="incomplete repetitions"):
        OperatingEnvelopeAnalyzer().analyze(sweep_reports=(candidate,))


def test_operating_envelope_rejects_non_soak_endurance() -> None:
    endurance = _endurance_report()
    benchmark = endurance["benchmark"]
    assert isinstance(benchmark, dict)
    benchmark["scenario"] = "idle"

    with pytest.raises(ValueError, match="scenario=soak"):
        OperatingEnvelopeAnalyzer().analyze(
            sweep_reports=(_sweep_report(),),
            endurance_reports=(endurance,),
        )


def test_operating_envelope_cli_writes_schema_valid_report(tmp_path: Path) -> None:
    sweep = tmp_path / "sweep.json"
    soak = tmp_path / "soak.json"
    output = tmp_path / "envelope.json"
    sweep.write_text(json.dumps(_sweep_report()), encoding="utf-8")
    soak.write_text(json.dumps(_endurance_report()), encoding="utf-8")

    assert (
        main(
            [
                "--sweep",
                str(sweep),
                "--endurance",
                str(soak),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["highest_verified_concurrency"] == 10
    assert payload["budget_status"] == "not-established"


def test_operating_envelope_cli_rejects_mismatched_evidence(tmp_path: Path) -> None:
    sweep = tmp_path / "sweep.json"
    soak = tmp_path / "soak.json"
    output = tmp_path / "envelope.json"
    sweep.write_text(json.dumps(_sweep_report()), encoding="utf-8")
    soak.write_text(json.dumps(_endurance_report(commit="different-sha")), encoding="utf-8")

    assert (
        main(
            [
                "--sweep",
                str(sweep),
                "--endurance",
                str(soak),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()
