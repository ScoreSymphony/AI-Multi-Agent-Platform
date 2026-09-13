from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking import (
    EnduranceBenchmarkSpec,
    SingleNodeEnduranceHarness,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-endurance.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def _idle_spec() -> EnduranceBenchmarkSpec:
    return EnduranceBenchmarkSpec(
        benchmark_id="single-node.idle.footprint",
        benchmark_version="1.0",
        scenario="idle",
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        duration_seconds=0.03,
        sample_interval_seconds=0.01,
        max_operations=0,
        concurrency=1,
        seed_tasks=0,
        warmup_operations=0,
        timeout_seconds=30.0,
        read_weight=0,
        write_weight=0,
    )


def _soak_spec() -> EnduranceBenchmarkSpec:
    return EnduranceBenchmarkSpec(
        benchmark_id="single-node.soak.mixed",
        benchmark_version="1.0",
        scenario="soak",
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        duration_seconds=5.0,
        sample_interval_seconds=0.01,
        max_operations=5,
        concurrency=2,
        seed_tasks=2,
        warmup_operations=1,
        timeout_seconds=30.0,
        read_weight=4,
        write_weight=1,
    )


def test_idle_footprint_is_correct_and_schema_valid(tmp_path: Path) -> None:
    async def run() -> None:
        report = await SingleNodeEnduranceHarness(
            SingleNodeConfig(data_dir=tmp_path / "idle", secure_cookie=False),
            platform_commit="idle-test-sha",
        ).run(_idle_spec())

        assert report.correctness.passed is True
        assert report.correctness.attempted_operations == 0
        assert report.correctness.observed_tasks == 0
        assert report.correctness.observed_runs == 0
        assert report.operation_latency.count == 0
        assert report.throughput_operations_per_second == 0
        assert report.startup_latency.count == 1
        assert len(report.snapshots) >= 2
        assert report.measurements["stop_reason"] == "duration"
        assert report.errors == ()
        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_bounded_soak_tracks_resource_drift_and_canonical_correctness(tmp_path: Path) -> None:
    async def run() -> None:
        report = await SingleNodeEnduranceHarness(
            SingleNodeConfig(data_dir=tmp_path / "soak", secure_cookie=False),
            platform_commit="soak-test-sha",
        ).run(_soak_spec())

        assert report.correctness.passed is True
        assert report.correctness.attempted_operations == 5
        assert report.correctness.completed_operations == 5
        assert report.correctness.failed_operations == 0
        assert report.correctness.seeded_tasks == 2
        assert report.correctness.observed_tasks >= 3
        assert report.correctness.observed_runs >= 3
        assert report.correctness.duplicate_write_task_ids == 0
        assert report.correctness.duplicate_write_run_ids == 0
        assert report.operation_latency.count == 5
        assert report.write_latency.count == 1
        assert report.read_latency.count == 4
        assert report.throughput_operations_per_second > 0
        assert len(report.snapshots) >= 2
        assert report.snapshots[-1].completed_operations == 5
        assert report.measurements["stop_reason"] == "operation-limit"
        assert isinstance(report.measurements["traced_memory_growth_bytes"], int)
        assert report.errors == ()
        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_endurance_spec_requires_bounded_idle_and_soak_contracts() -> None:
    with pytest.raises(ValueError, match="idle benchmark cannot"):
        EnduranceBenchmarkSpec(
            benchmark_id="invalid-idle",
            benchmark_version="1.0",
            scenario="idle",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            duration_seconds=1.0,
            sample_interval_seconds=0.1,
            max_operations=1,
            concurrency=1,
            seed_tasks=0,
            warmup_operations=0,
            timeout_seconds=30.0,
            read_weight=0,
            write_weight=0,
        )

    with pytest.raises(ValueError, match="max_operations"):
        EnduranceBenchmarkSpec(
            benchmark_id="invalid-soak",
            benchmark_version="1.0",
            scenario="soak",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            duration_seconds=1.0,
            sample_interval_seconds=0.1,
            max_operations=0,
            concurrency=1,
            seed_tasks=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            read_weight=1,
            write_weight=0,
        )
