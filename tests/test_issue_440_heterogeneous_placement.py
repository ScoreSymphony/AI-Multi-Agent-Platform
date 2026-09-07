from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.heterogeneous_placement import (
    HETEROGENEOUS_PLACEMENT_REPORT_SCHEMA_VERSION,
    HeterogeneousPlacementBenchmarkHarness,
    HeterogeneousPlacementSpec,
)


def test_heterogeneous_placement_routes_and_rejects_canonically(tmp_path: Path) -> None:
    report = HeterogeneousPlacementBenchmarkHarness(
        tmp_path / "benchmark",
        platform_commit="test-sha",
    ).run(HeterogeneousPlacementSpec(iterations_per_profile=2))

    assert report.schema_version == HETEROGENEOUS_PLACEMENT_REPORT_SCHEMA_VERSION
    assert report.platform_commit == "test-sha"
    assert report.correctness.passed is True
    assert report.correctness.expected_operations == 8
    assert report.correctness.attempted_operations == 8
    assert report.correctness.successful_placements == 6
    assert report.correctness.expected_successful_placements == 6
    assert report.correctness.rejected_operations == 2
    assert report.correctness.expected_rejected_operations == 2
    assert report.correctness.misplaced_operations == 0
    assert report.correctness.reservation_leaks == 0
    assert report.role_placement_counts == {"browser": 2, "cpu": 2, "gpu": 2}
    assert report.profile_latency["cpu-only"].count == 2
    assert report.profile_latency["gpu-inference"].count == 2
    assert report.profile_latency["browser-network"].count == 2
    assert report.profile_latency["unschedulable-vram"].count == 2
    assert report.rejection_code_counts["vram_insufficient"] >= 2
    assert set(report.worker_roles.values()) == {"browser", "cpu", "gpu"}
    assert len(report.node_ids) == 3
    assert len(report.worker_ids) == 3
    assert report.errors == ()

    document = report.to_dict()
    schema = json.loads(
        Path("docs/schemas/benchmark-heterogeneous-placement.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


def test_heterogeneous_placement_spec_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="iterations_per_profile"):
        HeterogeneousPlacementSpec(iterations_per_profile=0)
    with pytest.raises(ValueError, match="safety_max_operations"):
        HeterogeneousPlacementSpec(safety_max_operations=0)
    with pytest.raises(ValueError, match="operation safety bound"):
        HeterogeneousPlacementSpec(iterations_per_profile=3, safety_max_operations=11)


def test_heterogeneous_placement_requires_fresh_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "occupied"
    data_root.mkdir()
    (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh"):
        HeterogeneousPlacementBenchmarkHarness(data_root).run(
            HeterogeneousPlacementSpec(iterations_per_profile=1)
        )
