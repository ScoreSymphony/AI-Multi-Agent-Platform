from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.transport_faults import (
    TransportFaultBenchmarkHarness,
    TransportFaultBenchmarkSpec,
)


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-transport-fault.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(payload, dict)
    return payload


def _spec(
    scenario: str,
    *,
    batch_size: int,
    max_queue_size: int,
    fault_operations: int,
) -> TransportFaultBenchmarkSpec:
    return TransportFaultBenchmarkSpec(
        benchmark_id=f"transport.{scenario}",
        benchmark_version="1.0",
        scenario=scenario,
        transport_profile="in-process-reference",
        batch_size=batch_size,
        concurrency=2,
        max_queue_size=max_queue_size,
        fault_operations=fault_operations,
        timeout_seconds=5.0,
    )


def test_backpressure_reports_expected_resource_exhaustion_and_recovers() -> None:
    async def run() -> None:
        report = await TransportFaultBenchmarkHarness(platform_commit="backpressure-sha").run(
            _spec(
                "backpressure",
                batch_size=2,
                max_queue_size=2,
                fault_operations=1,
            )
        )

        assert report.correctness.passed is True
        assert report.correctness.attempted_publishes == 4
        assert report.correctness.accepted_publishes == 3
        assert report.correctness.expected_failures == 1
        assert report.correctness.unexpected_failures == 0
        assert report.correctness.delivery_attempts == 3
        assert report.correctness.unique_delivered_message_ids == 3
        assert report.correctness.message_loss_count == 0
        assert report.correctness.recovered is True
        assert report.error_counts == {"resource_exhausted": 1}
        assert report.recovery_latency.count == 1
        assert report.errors == ()
        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_outage_rejects_fault_window_then_recovers_without_message_loss() -> None:
    async def run() -> None:
        report = await TransportFaultBenchmarkHarness(platform_commit="outage-sha").run(
            _spec(
                "outage",
                batch_size=2,
                max_queue_size=4,
                fault_operations=2,
            )
        )

        assert report.correctness.passed is True
        assert report.correctness.attempted_publishes == 6
        assert report.correctness.accepted_publishes == 4
        assert report.correctness.expected_failures == 2
        assert report.correctness.unexpected_failures == 0
        assert report.correctness.delivery_attempts == 4
        assert report.correctness.unique_delivered_message_ids == 4
        assert report.correctness.message_loss_count == 0
        assert report.correctness.recovered is True
        assert report.error_counts == {"unavailable": 2}
        assert report.recovery_latency.count == 1
        assert report.errors == ()
        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_duplicate_delivery_is_visible_but_handler_remains_idempotent() -> None:
    async def run() -> None:
        report = await TransportFaultBenchmarkHarness(platform_commit="duplicate-sha").run(
            _spec(
                "duplicate-delivery",
                batch_size=3,
                max_queue_size=4,
                fault_operations=0,
            )
        )

        assert report.correctness.passed is True
        assert report.correctness.attempted_publishes == 4
        assert report.correctness.accepted_publishes == 4
        assert report.correctness.expected_failures == 0
        assert report.correctness.unexpected_failures == 0
        assert report.correctness.delivery_attempts == 5
        assert report.correctness.unique_delivered_message_ids == 3
        assert report.correctness.handler_executions == 3
        assert report.correctness.suppressed_duplicate_handlers == 1
        assert report.correctness.redelivered_deliveries == 1
        assert report.correctness.expected_duplicate_delivery_attempts == 2
        assert report.correctness.observed_duplicate_delivery_attempts == 2
        assert report.correctness.message_loss_count == 0
        assert report.correctness.recovered is True
        assert report.error_counts == {}
        assert report.errors == ()
        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


def test_transport_fault_specs_reject_incoherent_scenario_bounds() -> None:
    with pytest.raises(ValueError, match="batch_size must equal max_queue_size"):
        _spec(
            "backpressure",
            batch_size=2,
            max_queue_size=3,
            fault_operations=1,
        )
    with pytest.raises(ValueError, match="queue must hold both accepted load phases"):
        _spec(
            "outage",
            batch_size=3,
            max_queue_size=5,
            fault_operations=1,
        )
    with pytest.raises(ValueError, match="does not use fault_operations"):
        _spec(
            "duplicate-delivery",
            batch_size=2,
            max_queue_size=3,
            fault_operations=1,
        )
