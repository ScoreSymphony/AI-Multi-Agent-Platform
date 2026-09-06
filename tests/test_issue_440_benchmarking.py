from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking import (
    BenchmarkSpec,
    LatencyDistribution,
    RegressionThresholds,
    SingleNodeBenchmarkHarness,
    compare_with_baseline,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig


def test_latency_distribution_is_deterministic() -> None:
    distribution = LatencyDistribution.from_seconds([0.001, 0.002, 0.003, 0.004, 0.005])

    assert distribution.count == 5
    assert distribution.min_ms == 1.0
    assert distribution.mean_ms == 3.0
    assert distribution.p50_ms == 3.0
    assert distribution.p95_ms == 4.8
    assert distribution.p99_ms == 4.96
    assert distribution.max_ms == 5.0


def test_single_node_benchmark_report_is_correct_and_schema_valid(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        report = await SingleNodeBenchmarkHarness(config, platform_commit="test-sha").run(
            BenchmarkSpec(
                benchmark_id="single-node.reference.lifecycle",
                benchmark_version="1.0",
                deployment_profile="single-node-reference",
                operation_count=3,
                concurrency=2,
                warmup_operations=1,
            )
        )

        assert report.correctness.passed is True
        assert report.correctness.completed_operations == 3
        assert report.correctness.failed_operations == 0
        assert report.correctness.duplicate_task_ids == 0
        assert report.correctness.duplicate_run_ids == 0
        assert report.correctness.timeline_failures == 0
        assert report.throughput_operations_per_second > 0
        assert report.operation_latency.count == 3
        assert report.admission_latency.count == 3
        assert report.execution_latency.count == 3
        assert report.inspection_latency.count == 3
        assert report.resources.storage_growth_bytes > 0
        assert report.platform_commit == "test-sha"
        assert "cpu_model" in report.environment
        assert "memory_total_bytes" in report.environment

        schema = json.loads(
            Path("docs/schemas/benchmark-report.v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(report.to_dict())

    asyncio.run(scenario())


def test_single_node_reference_benchmark_requires_fresh_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "platform"
    data_root.mkdir()
    (data_root / "unrelated-state.txt").write_text("do not touch", encoding="utf-8")

    async def scenario() -> None:
        harness = SingleNodeBenchmarkHarness(
            SingleNodeConfig(data_dir=data_root, secure_cookie=False)
        )
        with pytest.raises(ValueError, match="fresh empty data root"):
            await harness.run(
                BenchmarkSpec(
                    benchmark_id="single-node.reference.lifecycle",
                    benchmark_version="1.0",
                    deployment_profile="single-node-reference",
                    operation_count=1,
                    concurrency=1,
                )
            )

    asyncio.run(scenario())
    assert (data_root / "unrelated-state.txt").read_text(encoding="utf-8") == "do not touch"


def test_baseline_comparison_requires_compatible_environment_and_explicit_budget(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        report = await SingleNodeBenchmarkHarness(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        ).run(
            BenchmarkSpec(
                benchmark_id="single-node.reference.lifecycle",
                benchmark_version="1.0",
                deployment_profile="single-node-reference",
                operation_count=1,
                concurrency=1,
            )
        )
        baseline = report.to_dict()

        no_budget = compare_with_baseline(report, baseline, RegressionThresholds())
        assert no_budget.comparable is True
        assert no_budget.classification == "comparable_no_budget"

        strict = compare_with_baseline(
            report,
            {
                **baseline,
                "operation_latency": {
                    **baseline["operation_latency"],
                    "p95_ms": max(report.operation_latency.p95_ms / 2.0, 0.001),
                },
            },
            RegressionThresholds(max_p95_latency_regression_ratio=0.10),
        )
        assert strict.comparable is True
        assert strict.classification == "regression"

        incompatible = compare_with_baseline(
            report,
            {
                **baseline,
                "benchmark": {**baseline["benchmark"], "concurrency": 999},
            },
            RegressionThresholds(max_p95_latency_regression_ratio=0.10),
        )
        assert incompatible.comparable is False
        assert incompatible.classification == "incomparable"

        different_workload_size = compare_with_baseline(
            report,
            {
                **baseline,
                "benchmark": {**baseline["benchmark"], "operation_count": 999},
            },
            RegressionThresholds(max_p95_latency_regression_ratio=0.10),
        )
        assert different_workload_size.comparable is False
        assert different_workload_size.classification == "incomparable"

    asyncio.run(scenario())
