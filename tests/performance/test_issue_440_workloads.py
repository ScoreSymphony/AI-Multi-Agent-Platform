from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking import (
    SingleNodeWorkloadHarness,
    WorkloadBenchmarkSpec,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig


def _spec(scenario: str) -> WorkloadBenchmarkSpec:
    identities = {
        "read-heavy": ("single-node.api.read-heavy", "list-detail-runs-timeline", 1, 0),
        "mixed": ("single-node.api.mixed", "weighted-read-write-lifecycle", 4, 1),
        "history": ("single-node.api.history", "large-state-query-mix", 1, 0),
        "restart": ("single-node.restart.accumulated-state", "restart-then-query", 1, 0),
    }
    benchmark_id, distribution, read_weight, write_weight = identities[scenario]
    return WorkloadBenchmarkSpec(
        benchmark_id=benchmark_id,
        benchmark_version="1.0",
        scenario=scenario,
        deployment_profile="single-node-reference",
        persistence_profile="sqlite-reference",
        workload_distribution=distribution,
        operation_count=5,
        concurrency=2,
        seed_tasks=2,
        warmup_operations=1,
        timeout_seconds=30.0,
        read_weight=read_weight,
        write_weight=write_weight,
    )


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-workload.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("scenario", ["read-heavy", "mixed", "history", "restart"])
def test_single_node_workload_profiles_are_correct_and_schema_valid(
    tmp_path: Path,
    scenario: str,
) -> None:
    async def run() -> None:
        report = await SingleNodeWorkloadHarness(
            SingleNodeConfig(data_dir=tmp_path / scenario, secure_cookie=False),
            platform_commit="workload-test-sha",
        ).run(_spec(scenario))

        assert report.correctness.passed is True
        assert report.correctness.completed_operations == 5
        assert report.correctness.failed_operations == 0
        assert report.correctness.seeded_tasks == 2
        assert report.correctness.observed_tasks >= 2
        assert report.correctness.observed_runs >= 2
        assert report.operation_latency.count == 5
        assert report.throughput_operations_per_second > 0
        assert report.platform_commit == "workload-test-sha"
        assert report.benchmark.persistence_profile == "sqlite-reference"
        assert report.benchmark.expected_invariants
        assert report.benchmark.captured_metrics
        assert report.sample_task_ids
        assert report.sample_run_ids
        assert report.errors == ()

        if scenario == "mixed":
            assert report.measurements["write_operations"] == 1
            assert report.measurements["read_operations"] == 4
            assert report.write_latency.count == 1
            assert report.correctness.observed_tasks >= 3
            assert report.correctness.observed_runs >= 3
        else:
            assert report.measurements["write_operations"] == 0
            assert report.read_latency.count == 5

        if scenario == "restart":
            assert report.restart_latency.count == 1
            assert report.restart_latency.p50_ms > 0
        else:
            assert report.restart_latency.count == 0

        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_workload_spec_rejects_invalid_mixed_and_repetition_contract() -> None:
    with pytest.raises(ValueError, match="write_weight"):
        WorkloadBenchmarkSpec(
            benchmark_id="invalid",
            benchmark_version="1.0",
            scenario="mixed",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            workload_distribution="invalid",
            operation_count=1,
            concurrency=1,
            seed_tasks=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            read_weight=1,
            write_weight=0,
        )

    with pytest.raises(ValueError, match="exactly one"):
        WorkloadBenchmarkSpec(
            benchmark_id="invalid",
            benchmark_version="1.0",
            scenario="read-heavy",
            deployment_profile="single-node-reference",
            persistence_profile="sqlite-reference",
            workload_distribution="invalid",
            operation_count=1,
            concurrency=1,
            seed_tasks=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            repetition_count=2,
        )
