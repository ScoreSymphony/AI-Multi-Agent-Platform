from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking import SingleNodeSweepHarness


def test_single_node_sweep_runs_independent_scale_points_and_validates_schema(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        execution = await SingleNodeSweepHarness(
            tmp_path / "data",
            platform_commit="sweep-test-sha",
        ).run(
            concurrency_levels=(1, 2),
            operation_count=2,
            warmup_operations=0,
            timeout_seconds=30.0,
            repetitions=1,
        )

        summary = execution.summary
        assert summary.correctness_passed is True
        assert summary.concurrency_levels == (1, 2)
        assert summary.operation_count_per_point == 2
        assert summary.persistence_profile == "sqlite-reference"
        assert summary.workload_distribution == "deterministic-task-lifecycle"
        assert len(summary.points) == 2
        assert [point.concurrency for point in summary.points] == [1, 2]
        assert all(point.correctness_passed for point in summary.points)
        assert all(point.completed_operations == 2 for point in summary.points)
        assert all(point.throughput_operations_per_second > 0 for point in summary.points)
        assert all(point.p95_latency_ms > 0 for point in summary.points)
        assert [point.report_file for point in summary.points] == [
            "c-1-r-1.json",
            "c-2-r-1.json",
        ]

        sweep_schema = json.loads(
            Path("docs/schemas/benchmark-sweep.v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(sweep_schema).validate(summary.to_dict())

        point_schema = json.loads(
            Path("docs/schemas/benchmark-report.v1.schema.json").read_text(encoding="utf-8")
        )
        for point, report in execution.point_reports:
            assert point.concurrency == report.benchmark.concurrency
            Draft202012Validator(point_schema).validate(report.to_dict())

    asyncio.run(scenario())


def test_single_node_sweep_repetitions_are_independent(tmp_path: Path) -> None:
    async def scenario() -> None:
        execution = await SingleNodeSweepHarness(tmp_path / "data").run(
            concurrency_levels=(1,),
            operation_count=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            repetitions=2,
        )

        assert [(point.concurrency, point.repetition) for point in execution.summary.points] == [
            (1, 1),
            (1, 2),
        ]
        roots = sorted(path.name for path in (tmp_path / "data").iterdir())
        assert roots == ["c-1-r-1", "c-1-r-2"]

    asyncio.run(scenario())


def test_single_node_sweep_rejects_invalid_levels_and_nonfresh_root(tmp_path: Path) -> None:
    async def invalid_levels() -> None:
        with pytest.raises(ValueError, match="unique"):
            await SingleNodeSweepHarness(tmp_path / "levels").run(
                concurrency_levels=(1, 1),
                operation_count=1,
                warmup_operations=0,
                timeout_seconds=30.0,
            )

    asyncio.run(invalid_levels())

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "keep.txt").write_text("not benchmark state", encoding="utf-8")

    async def nonfresh() -> None:
        with pytest.raises(ValueError, match="fresh empty"):
            await SingleNodeSweepHarness(occupied).run(
                concurrency_levels=(1,),
                operation_count=1,
                warmup_operations=0,
                timeout_seconds=30.0,
            )

    asyncio.run(nonfresh())
