from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.distributed_faults import (
    DISTRIBUTED_FAULT_REPORT_SCHEMA_VERSION,
    DistributedFaultSpec,
    DistributedWorkerWorkspaceFaultHarness,
)


def test_distributed_fault_profile_covers_worker_loss_rejoin_and_workspace_recovery(
    tmp_path: Path,
) -> None:
    report = asyncio.run(
        DistributedWorkerWorkspaceFaultHarness(
            tmp_path / "benchmark",
            platform_commit="test-sha",
        ).run(
            DistributedFaultSpec(
                worker_count=3,
                pre_fault_rounds=1,
                degraded_rounds=1,
                post_rejoin_rounds=1,
                payload_bytes=1024,
                timeout_seconds=20.0,
            )
        )
    )

    assert report.schema_version == DISTRIBUTED_FAULT_REPORT_SCHEMA_VERSION
    assert report.platform_commit == "test-sha"
    assert report.benchmark.pre_fault_jobs == 3
    assert report.benchmark.degraded_jobs == 2
    assert report.benchmark.post_rejoin_jobs == 3
    assert report.benchmark.expected_successful_jobs == 9
    assert report.benchmark.total_attempts == 10

    correctness = report.correctness
    assert correctness.passed is True
    assert correctness.expected_workers == 3
    assert correctness.stable_worker_ids == 3
    assert correctness.lost_worker_offline is True
    assert correctness.degraded_jobs == 2
    assert correctness.degraded_terminal_jobs == 2
    assert correctness.degraded_avoided_lost_worker is True
    assert correctness.rejoined_worker_online is True
    assert correctness.rejoin_preserved_identity is True
    assert correctness.post_rejoin_jobs == 3
    assert correctness.post_rejoin_terminal_jobs == 3
    assert correctness.post_rejoin_used_rejoined_worker is True
    assert correctness.workspace_failure_observed is True
    assert correctness.workspace_failure_code == "unavailable"
    assert correctness.workspace_failure_retryable is True
    assert correctness.workspace_failure_record_lost is True
    assert correctness.workspace_failure_reached_execution is False
    assert correctness.workspace_recovery_terminal is True
    assert correctness.workspace_cleanup_succeeded is True
    assert correctness.expected_successful_jobs == 9
    assert correctness.terminal_successful_jobs == 9
    assert correctness.duplicate_worker_job_ids == 0
    assert correctness.duplicate_run_ids == 0

    assert report.pre_fault_dispatch_latency.count == 3
    assert report.worker_loss_reconciliation_latency.count == 1
    assert report.degraded_dispatch_latency.count == 2
    assert report.worker_rejoin_latency.count == 1
    assert report.post_rejoin_dispatch_latency.count == 3
    assert report.workspace_failure_latency.count == 1
    assert report.workspace_recovery_dispatch_latency.count == 1
    assert report.lost_worker_id in report.worker_ids
    assert len(report.worker_ids) == 3
    assert len(report.worker_job_ids) == 10
    assert len(report.run_ids) == 10
    assert len(set(report.worker_job_ids)) == 10
    assert len(set(report.run_ids)) == 10
    assert report.errors == ()

    document = report.to_dict()
    schema = json.loads(
        Path("docs/schemas/benchmark-distributed-faults.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"worker_count": 1}, "worker_count"),
        ({"pre_fault_rounds": 0}, "pre_fault_rounds"),
        ({"degraded_rounds": 0}, "degraded_rounds"),
        ({"post_rejoin_rounds": 0}, "post_rejoin_rounds"),
        ({"payload_bytes": 0}, "payload_bytes"),
        ({"heartbeat_timeout_seconds": 0.0}, "heartbeat_timeout_seconds"),
        ({"reservation_ttl_seconds": 0.0}, "reservation_ttl_seconds"),
        ({"timeout_seconds": 0.0}, "timeout_seconds"),
        ({"safety_max_operations": 0}, "safety_max_operations"),
        ({"safety_max_payload_bytes": 0}, "safety_max_payload_bytes"),
    ],
)
def test_distributed_fault_spec_rejects_invalid_bounds(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        DistributedFaultSpec(**kwargs)  # type: ignore[arg-type]


def test_distributed_fault_spec_rejects_operation_and_payload_safety_limits() -> None:
    with pytest.raises(ValueError, match="operation safety bound"):
        DistributedFaultSpec(
            worker_count=3,
            safety_max_operations=9,
        )
    with pytest.raises(ValueError, match="payload safety bound"):
        DistributedFaultSpec(
            payload_bytes=2048,
            safety_max_payload_bytes=1024,
        )


def test_distributed_fault_profile_requires_fresh_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "occupied"
    data_root.mkdir()
    (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh"):
        asyncio.run(DistributedWorkerWorkspaceFaultHarness(data_root).run(DistributedFaultSpec()))
