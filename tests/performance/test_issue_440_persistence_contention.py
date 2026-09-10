from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.persistence_contention import (
    PERSISTENCE_CONTENTION_REPORT_SCHEMA_VERSION,
    PersistenceContentionBenchmarkHarness,
    PersistenceContentionBenchmarkSpec,
)


def test_sqlite_contention_profile_uses_competing_canonical_writers(tmp_path: Path) -> None:
    async def scenario() -> None:
        report = await PersistenceContentionBenchmarkHarness(
            tmp_path / "benchmark",
            platform_commit="test-sha",
        ).run(
            PersistenceContentionBenchmarkSpec(
                writer_count=3,
                tasks_per_writer=2,
                safety_max_writers=3,
                safety_max_tasks_per_writer=2,
            )
        )

        assert report.schema_version == PERSISTENCE_CONTENTION_REPORT_SCHEMA_VERSION
        assert report.platform_commit == "test-sha"
        assert report.correctness.passed is True
        assert report.correctness.expected_tasks == 6
        assert report.correctness.completed_tasks == 6
        assert report.correctness.expected_logical_operations == 12
        assert report.correctness.successful_logical_operations == 12
        assert report.correctness.failed_logical_operations == 0
        assert report.correctness.task_state_failures == 0
        assert report.correctness.history_failures == 0
        assert report.correctness.command_record_failures == 0
        assert report.correctness.stream_set_failures == 0

        assert report.contention.writer_instances == 3
        assert report.contention.synchronized_rounds == 4
        assert report.contention.peak_in_flight_operations >= 1
        assert report.contention.sqlite_busy_errors == 0
        assert report.contention.contract_conflicts == 0
        assert report.contention.unexpected_errors == 0
        assert report.contention.per_writer_successful_operations == (4, 4, 4)
        assert report.operation_latency.count == 12
        assert report.throughput_logical_operations_per_second > 0
        assert report.errors == ()

        schema = json.loads(
            Path("docs/schemas/benchmark-persistence-contention.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report.to_dict())

    asyncio.run(scenario())


def test_sqlite_contention_spec_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="writer_count"):
        PersistenceContentionBenchmarkSpec(writer_count=1)
    with pytest.raises(ValueError, match="tasks_per_writer"):
        PersistenceContentionBenchmarkSpec(tasks_per_writer=0)
    with pytest.raises(ValueError, match="barrier_timeout_seconds"):
        PersistenceContentionBenchmarkSpec(barrier_timeout_seconds=0)
    with pytest.raises(ValueError, match="safety_max_writers"):
        PersistenceContentionBenchmarkSpec(safety_max_writers=1)
    with pytest.raises(ValueError, match="safety_max_tasks_per_writer"):
        PersistenceContentionBenchmarkSpec(safety_max_tasks_per_writer=0)
    with pytest.raises(ValueError, match="writer_count exceeds"):
        PersistenceContentionBenchmarkSpec(writer_count=4, safety_max_writers=3)
    with pytest.raises(ValueError, match="tasks_per_writer exceeds"):
        PersistenceContentionBenchmarkSpec(
            tasks_per_writer=4,
            safety_max_tasks_per_writer=3,
        )


def test_sqlite_contention_profile_requires_fresh_data_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "occupied"
        data_root.mkdir()
        (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
        with pytest.raises(ValueError, match="fresh"):
            await PersistenceContentionBenchmarkHarness(data_root).run(
                PersistenceContentionBenchmarkSpec(writer_count=2, tasks_per_writer=1)
            )

    asyncio.run(scenario())
