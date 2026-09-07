from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.ha_failover import (
    HA_FAILOVER_REPORT_SCHEMA_VERSION,
    HAFailoverBenchmarkHarness,
    HAFailoverBenchmarkSpec,
)


def test_ha_failover_profile_preserves_identity_and_fails_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        report = await HAFailoverBenchmarkHarness(
            tmp_path / "benchmark",
            platform_commit="test-sha",
        ).run(
            HAFailoverBenchmarkSpec(
                active_task_count=2,
                repetitions=2,
                safety_max_tasks=4,
            )
        )

        assert report.schema_version == HA_FAILOVER_REPORT_SCHEMA_VERSION
        assert report.platform_commit == "test-sha"
        assert report.correctness.passed is True
        assert report.correctness.expected_repetitions == 2
        assert report.correctness.completed_repetitions == 2
        assert report.correctness.expected_tasks == 4
        assert report.correctness.prepared_tasks == 4
        assert report.correctness.recovered_tasks == 4
        assert report.correctness.task_identity_failures == 0
        assert report.correctness.run_identity_failures == 0
        assert report.correctness.duplicate_lifecycle_dispatches == 0
        assert report.correctness.history_failures == 0
        assert report.correctness.reconciled_stale_reservations == 4
        assert report.correctness.reconciliation_failures == 0
        assert report.correctness.stale_leader_rejections == 2
        assert report.correctness.outage_fail_closed_rejections == 2
        assert report.correctness.recovery_promotions == 2
        assert report.correctness.epoch_monotonicity_failures == 0
        assert report.promotion_latency.count == 2
        assert report.stale_leader_rejection_latency.count == 2
        assert report.replay_task_latency.count == 4
        assert report.coordination_outage_rejection_latency.count == 2
        assert report.coordination_recovery_promotion_latency.count == 2
        assert len(report.epoch_evidence) == 2
        assert all(
            evidence.initial_epoch < evidence.promoted_epoch < evidence.recovery_epoch
            for evidence in report.epoch_evidence
        )
        assert report.errors == ()

        document = report.to_dict()
        schema = json.loads(
            Path("docs/schemas/benchmark-ha-failover.v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(document)

    asyncio.run(scenario())


def test_ha_failover_timeout_returns_structured_failure_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def delayed_iteration(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(HAFailoverBenchmarkHarness, "_run_iteration", delayed_iteration)

    async def scenario() -> None:
        report = await HAFailoverBenchmarkHarness(tmp_path / "timeout").run(
            HAFailoverBenchmarkSpec(
                active_task_count=1,
                repetitions=1,
                timeout_seconds=0.001,
                safety_max_tasks=1,
            )
        )

        assert report.correctness.passed is False
        assert report.correctness.completed_repetitions == 0
        assert report.epoch_evidence == ()
        assert report.errors == ("HA failover benchmark timed out after 0.001 seconds",)

        schema = json.loads(
            Path("docs/schemas/benchmark-ha-failover.v1.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(report.to_dict())

    asyncio.run(scenario())


def test_ha_failover_spec_rejects_invalid_bounds() -> None:
    with pytest.raises(ValueError, match="active_task_count"):
        HAFailoverBenchmarkSpec(active_task_count=0)
    with pytest.raises(ValueError, match="repetitions"):
        HAFailoverBenchmarkSpec(repetitions=0)
    with pytest.raises(ValueError, match="lease_ttl_seconds"):
        HAFailoverBenchmarkSpec(lease_ttl_seconds=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        HAFailoverBenchmarkSpec(timeout_seconds=0)
    with pytest.raises(ValueError, match="safety_max_tasks"):
        HAFailoverBenchmarkSpec(safety_max_tasks=0)
    with pytest.raises(ValueError, match="task safety bound"):
        HAFailoverBenchmarkSpec(active_task_count=3, repetitions=2, safety_max_tasks=5)


def test_ha_failover_requires_fresh_data_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        data_root = tmp_path / "occupied"
        data_root.mkdir()
        (data_root / "existing.txt").write_text("occupied", encoding="utf-8")
        with pytest.raises(ValueError, match="fresh"):
            await HAFailoverBenchmarkHarness(data_root).run(
                HAFailoverBenchmarkSpec(active_task_count=1, repetitions=1)
            )

    asyncio.run(scenario())
