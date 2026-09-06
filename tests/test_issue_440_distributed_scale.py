from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.distributed_scale import (
    DISTRIBUTED_SCALE_REPORT_SCHEMA_VERSION,
    DistributedScaleSpec,
    DistributedWorkerWorkspaceScaleHarness,
)


def test_distributed_scale_runs_authenticated_two_worker_workspace_round(tmp_path: Path) -> None:
    report = asyncio.run(
        DistributedWorkerWorkspaceScaleHarness(
            tmp_path / "benchmark",
            platform_commit="test-sha",
        ).run(
            DistributedScaleSpec(
                worker_count=2,
                rounds=1,
                payload_sizes_bytes=(1024,),
                timeout_seconds=20.0,
            )
        )
    )

    assert report.schema_version == DISTRIBUTED_SCALE_REPORT_SCHEMA_VERSION
    assert report.platform_commit == "test-sha"
    assert report.correctness.passed is True
    assert report.correctness.expected_workers == 2
    assert report.correctness.registered_workers == 2
    assert report.correctness.heartbeat_workers == 2
    assert report.correctness.expected_jobs == 2
    assert report.correctness.terminal_jobs == 2
    assert report.correctness.unique_worker_job_ids == 2
    assert report.correctness.unique_run_ids == 2
    assert report.correctness.workers_used == 2
    assert report.correctness.balanced_rounds == 1
    assert report.correctness.cleaned_materializations == 2
    assert report.registration_latency.count == 2
    assert report.heartbeat_latency.count == 2
    assert report.dispatch_latency.count == 2
    assert report.terminal_latency.count == 2
    assert set(report.placement_counts) == set(report.worker_ids)
    assert set(report.placement_counts.values()) == {1}
    assert report.payload_operation_counts == {"1024": 2}
    assert len(report.node_ids) == 2
    assert len(report.worker_ids) == 2
    assert len(report.worker_job_ids) == 2
    assert len(report.run_ids) == 2
    assert report.errors == ()

    document = report.to_dict()
    schema = json.loads(
        Path("docs/schemas/benchmark-distributed-scale.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"worker_count": 0}, "worker_count"),
        ({"rounds": 0}, "rounds"),
        ({"payload_sizes_bytes": ()}, "payload_sizes_bytes"),
        ({"payload_sizes_bytes": (1024, 1024)}, "unique"),
        ({"payload_sizes_bytes": (2048, 1024)}, "strictly increasing"),
        ({"payload_sizes_bytes": (0,)}, "positive"),
        ({"chunk_bytes": 1024}, "fixed 64 KiB"),
        ({"timeout_seconds": 0.0}, "timeout_seconds"),
    ],
)
def test_distributed_scale_spec_rejects_invalid_bounds(
    kwargs: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "worker_count": 1,
        "rounds": 1,
        "payload_sizes_bytes": (1024,),
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        DistributedScaleSpec(**values)  # type: ignore[arg-type]


def test_distributed_scale_spec_rejects_operation_and_payload_safety_limits() -> None:
    with pytest.raises(ValueError, match="operation safety bound"):
        DistributedScaleSpec(
            worker_count=2,
            rounds=2,
            payload_sizes_bytes=(1024,),
            safety_max_operations=3,
        )
    with pytest.raises(ValueError, match="payload safety bound"):
        DistributedScaleSpec(
            worker_count=1,
            rounds=1,
            payload_sizes_bytes=(2048,),
            safety_max_payload_bytes=1024,
        )


def test_distributed_scale_requires_fresh_data_root(tmp_path: Path) -> None:
    data_root = tmp_path / "occupied"
    data_root.mkdir()
    (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh"):
        asyncio.run(
            DistributedWorkerWorkspaceScaleHarness(data_root).run(
                DistributedScaleSpec(
                    worker_count=1,
                    rounds=1,
                    payload_sizes_bytes=(1024,),
                )
            )
        )
