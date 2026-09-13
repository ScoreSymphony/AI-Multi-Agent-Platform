from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.persistence_faults import (
    PERSISTENCE_FAULT_REPORT_SCHEMA_VERSION,
    PersistenceFaultBenchmarkHarness,
    PersistenceFaultBenchmarkSpec,
)


def test_persistence_fault_profile_recovers_before_and_after_commit_faults(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        report = await PersistenceFaultBenchmarkHarness(
            tmp_path / "benchmark",
            platform_commit="test-sha",
        ).run(
            PersistenceFaultBenchmarkSpec(
                task_count=4,
                concurrency=2,
                failure_mode="mixed",
                failure_every=2,
                max_attempts=3,
                safety_max_tasks=4,
            )
        )

        assert report.schema_version == PERSISTENCE_FAULT_REPORT_SCHEMA_VERSION
        assert report.platform_commit == "test-sha"
        assert report.correctness.passed is True
        assert report.correctness.expected_tasks == 4
        assert report.correctness.completed_tasks == 4
        assert report.correctness.expected_logical_operations == 8
        assert report.correctness.successful_logical_operations == 8
        assert report.correctness.retryable_failures_observed == 4
        assert report.correctness.recovered_faulted_operations == 4
        assert report.correctness.exhausted_retries == 0
        assert report.correctness.unexpected_failures == 0
        assert report.correctness.task_state_failures == 0
        assert report.correctness.history_failures == 0
        assert report.correctness.command_record_failures == 0
        assert report.correctness.stream_set_failures == 0

        assert report.faults.planned_faults == 4
        assert report.faults.injected_failures == 4
        assert report.faults.injected_before_commit == 2
        assert report.faults.injected_after_commit == 2
        assert report.logical_operation_latency.count == 8
        assert report.attempt_latency.count == 12
        assert report.recovery_latency.count == 4
        assert report.errors == ()

        schema = json.loads(
            Path("docs/schemas/benchmark-persistence-fault.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(report.to_dict())

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_mode", ["before-commit", "after-commit"])
def test_persistence_fault_profile_recovers_each_fault_boundary(
    tmp_path: Path,
    failure_mode: str,
) -> None:
    async def scenario() -> None:
        report = await PersistenceFaultBenchmarkHarness(
            tmp_path / failure_mode,
        ).run(
            PersistenceFaultBenchmarkSpec(
                task_count=2,
                concurrency=1,
                failure_mode=failure_mode,
                failure_every=1,
                max_attempts=2,
                safety_max_tasks=2,
            )
        )

        assert report.correctness.passed is True
        assert report.faults.planned_faults == 4
        assert report.faults.injected_failures == 4
        assert report.correctness.recovered_faulted_operations == 4
        assert report.correctness.history_failures == 0
        assert report.correctness.command_record_failures == 0

        if failure_mode == "before-commit":
            assert report.faults.injected_before_commit == 4
            assert report.faults.injected_after_commit == 0
            assert report.faults.repository_commit_calls == 8
            assert report.faults.delegated_commit_calls == 4
        else:
            assert report.faults.injected_before_commit == 0
            assert report.faults.injected_after_commit == 4
            assert report.faults.repository_commit_calls == 4
            assert report.faults.delegated_commit_calls == 4

    asyncio.run(scenario())


def test_persistence_fault_spec_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="task_count"):
        PersistenceFaultBenchmarkSpec(task_count=0)
    with pytest.raises(ValueError, match="concurrency"):
        PersistenceFaultBenchmarkSpec(concurrency=0)
    with pytest.raises(ValueError, match="failure_mode"):
        PersistenceFaultBenchmarkSpec(failure_mode="unsupported")
    with pytest.raises(ValueError, match="failure_every"):
        PersistenceFaultBenchmarkSpec(failure_every=0)
    with pytest.raises(ValueError, match="max_attempts"):
        PersistenceFaultBenchmarkSpec(max_attempts=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        PersistenceFaultBenchmarkSpec(timeout_seconds=0)
    with pytest.raises(ValueError, match="task_count exceeds"):
        PersistenceFaultBenchmarkSpec(task_count=3, safety_max_tasks=2)
    with pytest.raises(ValueError, match="concurrency exceeds"):
        PersistenceFaultBenchmarkSpec(concurrency=3, safety_max_concurrency=2)
    with pytest.raises(ValueError, match="max_attempts exceeds"):
        PersistenceFaultBenchmarkSpec(max_attempts=4, safety_max_attempts=3)


def test_persistence_fault_profile_requires_fresh_data_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "occupied"
        data_root.mkdir()
        (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
        with pytest.raises(ValueError, match="fresh"):
            await PersistenceFaultBenchmarkHarness(data_root).run(
                PersistenceFaultBenchmarkSpec(task_count=1, safety_max_tasks=1)
            )

    asyncio.run(scenario())
