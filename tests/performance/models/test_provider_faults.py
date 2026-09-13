from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.provider_faults import (
    ProviderFaultBenchmarkHarness,
    ProviderFaultBenchmarkSpec,
)


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-provider-fault.v1.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize(
    ("scenario", "expected_code", "expected_retryable"),
    [
        ("model-latency", None, None),
        ("model-unavailable", "unavailable", True),
        ("model-cancelled", "cancelled", False),
        ("tool-unavailable", "unavailable", True),
        ("tool-timeout", "timeout", True),
        ("tool-cancelled", "cancelled", True),
    ],
)
def test_provider_fault_profiles_are_correct_and_schema_valid(
    scenario: str,
    expected_code: str | None,
    expected_retryable: bool | None,
) -> None:
    async def run() -> None:
        spec = ProviderFaultBenchmarkSpec(
            benchmark_id=f"provider.reference.{scenario}",
            benchmark_version="1.0",
            scenario=scenario,
            operations_per_phase=2,
            concurrency=2,
            fault_delay_seconds=0.02,
            tool_timeout_seconds=0.005,
            cancel_after_seconds=0.005,
            operation_timeout_seconds=1.0,
            safety_max_operations_per_phase=4,
            safety_max_concurrency=4,
        )
        report = await ProviderFaultBenchmarkHarness(platform_commit="provider-test-sha").run(spec)

        assert report.platform_commit == "provider-test-sha"
        assert report.expected_fault_error_code == expected_code
        assert report.correctness.passed is True
        assert report.correctness.attempted_operations == 6
        assert report.correctness.baseline_successes == 2
        assert report.correctness.recovery_successes == 2
        assert report.correctness.automatic_retry_attempts == 0
        assert report.correctness.unexpected_failures == 0
        assert report.errors == ()

        for phase in ("baseline", "fault", "recovery"):
            assert report.phase_latency[phase].count == 2
            assert report.provider_service_latency[phase].count == 2
            assert report.platform_overhead_latency[phase].count == 2
            assert report.phase_throughput_operations_per_second[phase] > 0

        if expected_code is None:
            assert report.correctness.fault_successes == 2
            assert report.correctness.expected_failures == 0
            assert report.correctness.retryable_failures == 0
            assert report.error_counts == {}
            assert report.retryable_error_counts == {}
        else:
            expected_retryable_failures = 2 if expected_retryable else 0
            expected_retryable_errors = {expected_code: 2} if expected_retryable else {}
            assert report.correctness.fault_successes == 0
            assert report.correctness.expected_failures == 2
            assert report.correctness.retryable_failures == expected_retryable_failures
            assert report.error_counts == {expected_code: 2}
            assert report.retryable_error_counts == expected_retryable_errors

        if scenario == "model-latency":
            assert report.provider_service_latency["fault"].p50_ms >= 10
        if scenario in {"model-cancelled", "tool-cancelled", "tool-timeout"}:
            assert report.correctness.provider_cancelled_calls == 2

        Draft202012Validator(_schema()).validate(report.to_dict())

    asyncio.run(run())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"operations_per_phase": 5, "safety_max_operations_per_phase": 4}, "operations"),
        ({"concurrency": 5, "safety_max_concurrency": 4}, "concurrency"),
        (
            {
                "scenario": "tool-timeout",
                "fault_delay_seconds": 0.005,
                "tool_timeout_seconds": 0.01,
            },
            "tool-timeout",
        ),
        (
            {
                "scenario": "model-cancelled",
                "fault_delay_seconds": 0.005,
                "cancel_after_seconds": 0.01,
            },
            "cancelled",
        ),
    ],
)
def test_provider_fault_spec_enforces_bounded_safe_fault_configuration(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "benchmark_id": "provider.reference.model-unavailable",
        "benchmark_version": "1.0",
        "scenario": "model-unavailable",
        "operations_per_phase": 2,
        "concurrency": 2,
        "fault_delay_seconds": 0.02,
        "tool_timeout_seconds": 0.005,
        "cancel_after_seconds": 0.005,
        "operation_timeout_seconds": 1.0,
        "safety_max_operations_per_phase": 4,
        "safety_max_concurrency": 4,
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        ProviderFaultBenchmarkSpec(**values)  # type: ignore[arg-type]
