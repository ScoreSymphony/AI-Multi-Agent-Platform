from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform.benchmarking.host_pressure_observer import (
    HostPressureObserverHarness,
    HostPressureObserverSpec,
)
from ai_multi_agent_platform.contracts import AdapterMetadata
from ai_multi_agent_platform.distributed.pressure import (
    HostPressureSnapshot,
    PressureKind,
    PressureSignal,
    PressureState,
)

BASE = datetime(2026, 9, 7, 18, 30, tzinfo=UTC)


class _SequenceProvider:
    def __init__(
        self,
        snapshots: list[HostPressureSnapshot],
    ) -> None:
        self._snapshots = snapshots
        self._index = 0

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot:
        assert node_id
        snapshot = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snapshot


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds

    def clock(self) -> datetime:
        return BASE + timedelta(seconds=self.value)


def _snapshot(state: PressureState, second: int) -> HostPressureSnapshot:
    signal_state = state if state is not PressureState.UNKNOWN else PressureState.HEALTHY
    return HostPressureSnapshot(
        state=state,
        observed_at=BASE + timedelta(seconds=second),
        signals=(
            PressureSignal(
                PressureKind.MEMORY,
                signal_state,
                0.75 if state is PressureState.ELEVATED else 0.25,
                "ratio",
            ),
        ),
        source_ref="fixture:host-pressure-observer",
        trusted=True,
        provider_metadata=(
            AdapterMetadata(
                namespace="linux.host_pressure",
                values={"psi.memory.some.avg10": float(second), "collector.read_only": True},
            ),
        ),
    )


def _schema() -> dict[str, object]:
    payload = json.loads(
        Path("docs/schemas/benchmark-host-pressure-observer.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(payload, dict)
    return payload


def test_observer_records_real_pressure_transitions_and_recovery_without_reservations() -> None:
    fake_time = _FakeTime()
    provider = _SequenceProvider(
        [
            _snapshot(PressureState.HEALTHY, 0),
            _snapshot(PressureState.ELEVATED, 1),
            HostPressureSnapshot(
                state=PressureState.CRITICAL,
                observed_at=BASE + timedelta(seconds=2),
                signals=(
                    PressureSignal(
                        PressureKind.PAGING,
                        PressureState.CRITICAL,
                        120.0,
                        "events_per_second",
                    ),
                ),
                source_ref="fixture:host-pressure-observer",
                trusted=True,
            ),
            _snapshot(PressureState.HEALTHY, 3),
        ]
    )
    spec = HostPressureObserverSpec(
        benchmark_id="host-pressure.linux.observer",
        benchmark_version="1.0",
        duration_seconds=3.0,
        sample_interval_seconds=1.0,
        max_samples=4,
        safety_max_duration_seconds=10.0,
    )

    report = HostPressureObserverHarness(
        provider,
        platform_commit="observer-test-sha",
        clock=fake_time.clock,
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
    ).run(spec)

    assert report.platform_commit == "observer-test-sha"
    assert report.summary.sample_count == 4
    assert report.summary.state_counts == {"healthy": 2, "elevated": 1, "critical": 1}
    assert report.summary.admission_action_counts == {
        "admit": 2,
        "queue": 1,
        "deny_temporarily": 1,
    }
    assert report.summary.scheduler_acceptance_counts == {"accepted": 2, "rejected": 2}
    assert report.summary.observed_pressure is True
    assert report.summary.recovered_after_pressure is True
    assert report.summary.recovery_latency_seconds == 2.0
    assert [item.to_state for item in report.transitions] == [
        "elevated",
        "critical",
        "healthy",
    ]
    assert report.observations[1].provider_metadata[0].namespace == "linux.host_pressure"
    assert report.correctness.active_reservations_after == 0
    assert report.correctness.passed is True
    assert report.safety.read_only_observer is True
    assert report.safety.host_mutation_attempted is False
    assert report.safety.destructive_load_generated is False
    assert report.errors == ()
    Draft202012Validator(_schema()).validate(report.to_dict())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"duration_seconds": 11.0}, "duration_seconds exceeds"),
        (
            {"duration_seconds": 4.0, "sample_interval_seconds": 1.0, "max_samples": 4},
            "max_samples",
        ),
        ({"sample_interval_seconds": 0.0}, "sample_interval_seconds"),
        ({"workload_class": " "}, "workload_class"),
    ],
)
def test_observer_spec_enforces_hard_safety_bounds(
    changes: dict[str, object],
    message: str,
) -> None:
    values: dict[str, object] = {
        "benchmark_id": "host-pressure.linux.observer",
        "benchmark_version": "1.0",
        "duration_seconds": 3.0,
        "sample_interval_seconds": 1.0,
        "max_samples": 4,
        "safety_max_duration_seconds": 10.0,
        "workload_class": "heavy",
    }
    values.update(changes)
    with pytest.raises(ValueError, match=message):
        HostPressureObserverSpec(**values)  # type: ignore[arg-type]
