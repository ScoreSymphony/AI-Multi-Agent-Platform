from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.persistence import SingleNodePersistenceScaleHarness


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-persistence-sweep.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


def test_persistence_growing_state_sweep_is_correct_and_schema_valid(tmp_path: Path) -> None:
    async def run() -> None:
        execution = await SingleNodePersistenceScaleHarness(
            tmp_path / "persistence-sweep",
            platform_commit="persistence-test-sha",
        ).run(
            seed_task_levels=(1, 2),
            operation_count=3,
            concurrency=1,
            warmup_operations=0,
            timeout_seconds=30.0,
            repetitions=1,
        )

        summary = execution.summary
        assert summary.correctness_passed is True
        assert summary.seed_task_levels == (1, 2)
        assert summary.platform_commit == "persistence-test-sha"
        assert len(summary.points) == 2
        assert len(execution.point_reports) == 2
        assert summary.errors == ()

        for point, report in execution.point_reports:
            assert point.correctness_passed is True
            assert point.seed_tasks in (1, 2)
            assert point.observed_tasks >= point.seed_tasks
            assert point.observed_runs >= point.seed_tasks
            assert point.storage_bytes_after > 0
            assert point.storage_bytes_per_seeded_task > 0
            assert point.restart_p50_latency_ms > 0
            assert point.read_p95_latency_ms >= 0
            assert point.report_file == f"state-{point.seed_tasks}-r-1.json"
            assert report.benchmark.scenario == "restart"
            assert report.benchmark.seed_tasks == point.seed_tasks
            assert report.correctness.passed is True
            assert report.restart_latency.count == 1
            assert report.read_latency.count == 3

        Draft202012Validator(_schema()).validate(summary.to_dict())

    asyncio.run(run())


@pytest.mark.parametrize(
    ("levels", "message"),
    [
        ((), "at least one"),
        ((0, 1), "positive"),
        ((1, 1), "unique"),
        ((2, 1), "strictly increasing"),
    ],
)
def test_persistence_sweep_rejects_invalid_state_levels(
    tmp_path: Path,
    levels: tuple[int, ...],
    message: str,
) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match=message):
            await SingleNodePersistenceScaleHarness(tmp_path / "invalid").run(
                seed_task_levels=levels,
                operation_count=1,
                concurrency=1,
                warmup_operations=0,
                timeout_seconds=30.0,
            )

    asyncio.run(run())


def test_persistence_sweep_requires_fresh_data_root(tmp_path: Path) -> None:
    root = tmp_path / "occupied"
    root.mkdir()
    (root / "existing.txt").write_text("occupied", encoding="utf-8")

    async def run() -> None:
        with pytest.raises(ValueError, match="fresh empty data root"):
            await SingleNodePersistenceScaleHarness(root).run(
                seed_task_levels=(1,),
                operation_count=1,
                concurrency=1,
                warmup_operations=0,
                timeout_seconds=30.0,
            )

    asyncio.run(run())
