from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking import (
    FaultUnderLoadSpec,
    SingleNodeFaultUnderLoadHarness,
    SingleNodeStressHarness,
    StressBenchmarkSpec,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig


def _schema(path: str) -> dict[str, object]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_single_node_stress_escalates_with_explicit_safety_bounds(tmp_path: Path) -> None:
    async def run() -> None:
        spec = StressBenchmarkSpec(
            benchmark_id="single-node.reference.lifecycle.stress",
            benchmark_version="1.0",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            concurrency_levels=(1, 2),
            operations_per_level=2,
            warmup_operations=0,
            timeout_seconds=30.0,
            safety_max_concurrency=4,
            safety_max_operations_per_level=4,
        )
        execution = await SingleNodeStressHarness(
            tmp_path / "stress",
            platform_commit="stress-test-sha",
        ).run(spec)

        assert execution.summary.correctness_passed is True
        assert execution.summary.requested_levels == (1, 2)
        assert execution.summary.completed_levels == (1, 2)
        assert execution.summary.stop_reason == "completed-levels"
        assert execution.summary.first_failed_concurrency is None
        assert len(execution.point_reports) == 2
        for point, report in execution.point_reports:
            assert point.correctness_passed is True
            assert point.failed_operations == 0
            assert point.error_rate == 0.0
            assert report.correctness.passed is True

        Draft202012Validator(_schema("docs/schemas/benchmark-stress.v1.schema.json")).validate(
            execution.summary.to_dict()
        )

    asyncio.run(run())


def test_stress_spec_rejects_requests_beyond_explicit_safety_limit() -> None:
    with pytest.raises(ValueError, match="concurrency exceeds explicit safety limit"):
        StressBenchmarkSpec(
            benchmark_id="stress",
            benchmark_version="1.0",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            concurrency_levels=(1, 8),
            operations_per_level=2,
            warmup_operations=0,
            timeout_seconds=30.0,
            safety_max_concurrency=4,
            safety_max_operations_per_level=4,
        )


def test_restart_fault_preserves_security_state_and_post_fault_load(tmp_path: Path) -> None:
    async def run() -> None:
        spec = FaultUnderLoadSpec(
            benchmark_id="single-node.fault.control-plane-restart-under-load",
            benchmark_version="1.0",
            scenario="control-plane-restart",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            operation_count=6,
            concurrency=2,
            fault_after_operations=3,
            seed_tasks=2,
            warmup_operations=0,
            timeout_seconds=30.0,
            safety_max_operations=10,
            safety_max_concurrency=4,
            read_weight=2,
            write_weight=1,
        )
        report = await SingleNodeFaultUnderLoadHarness(
            SingleNodeConfig(data_dir=tmp_path / "fault", secure_cookie=False),
            platform_commit="fault-test-sha",
        ).run_fault(spec)

        assert report.correctness.passed is True
        assert report.correctness.attempted_operations == 6
        assert report.correctness.completed_operations == 6
        assert report.correctness.failed_operations == 0
        assert report.correctness.pre_fault_completed_operations == 3
        assert report.correctness.post_fault_completed_operations == 3
        assert report.correctness.authentication_preserved is True
        assert report.correctness.unauthorized_access_blocked is True
        assert report.correctness.health_recovered is True
        assert report.correctness.readiness_recovered is True
        assert report.correctness.duplicate_write_task_ids == 0
        assert report.correctness.duplicate_write_run_ids == 0
        assert report.restart_latency.count == 1
        assert report.restart_latency.p50_ms > 0
        assert report.measurements["write_operations"] == 2
        assert report.measurements["read_operations"] == 4
        assert report.errors == ()

        Draft202012Validator(
            _schema("docs/schemas/benchmark-fault-under-load.v1.schema.json")
        ).validate(report.to_dict())

    asyncio.run(run())


def test_fault_spec_requires_fault_to_split_bounded_workload() -> None:
    with pytest.raises(ValueError, match="split the measured workload"):
        FaultUnderLoadSpec(
            benchmark_id="fault",
            benchmark_version="1.0",
            scenario="control-plane-restart",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            operation_count=4,
            concurrency=2,
            fault_after_operations=4,
            seed_tasks=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            safety_max_operations=10,
            safety_max_concurrency=4,
        )
