from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.benchmarking.host_pressure_observer import (
    HostPressureObserverHarness,
    HostPressureObserverSpec,
)
from ai_multi_agent_platform.distributed.pressure import (
    HostPressureSnapshot,
    PressureKind,
    PressureSignal,
    PressureState,
)

BASE = datetime(2026, 9, 7, 19, 0, tzinfo=UTC)


class _FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleep_calls: list[float] = []
        self.sleep_end_offsets: list[float] = []

    def monotonic(self) -> float:
        return self.value

    def clock(self) -> datetime:
        return BASE + timedelta(seconds=self.value)

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.value += seconds
        self.sleep_end_offsets.append(self.value)


class _SlowSequenceProvider:
    def __init__(
        self,
        fake_time: _FakeTime,
        states: list[PressureState],
        *,
        collection_seconds: float,
    ) -> None:
        self._fake_time = fake_time
        self._states = states
        self._collection_seconds = collection_seconds
        self._index = 0

    def snapshot_for_node(self, node_id: str) -> HostPressureSnapshot:
        assert node_id
        self._fake_time.advance(self._collection_seconds)
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1
        return HostPressureSnapshot(
            state=state,
            observed_at=self._fake_time.clock(),
            signals=(
                PressureSignal(
                    PressureKind.MEMORY,
                    state,
                    0.25 if state is PressureState.HEALTHY else 0.85,
                    "ratio",
                ),
            ),
            source_ref="fixture:slow-host-pressure-observer",
            trusted=True,
        )


def test_slow_collection_keeps_sampling_anchored_and_captures_deadline_recovery() -> None:
    fake_time = _FakeTime()
    provider = _SlowSequenceProvider(
        fake_time,
        [
            PressureState.ELEVATED,
            PressureState.CRITICAL,
            PressureState.ELEVATED,
            PressureState.HEALTHY,
        ],
        collection_seconds=0.4,
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
        clock=fake_time.clock,
        monotonic=fake_time.monotonic,
        sleeper=fake_time.sleep,
    ).run(spec)

    assert [sample.offset_seconds for sample in report.observations] == [0.0, 1.0, 2.0, 3.0]
    assert fake_time.sleep_calls == pytest.approx([0.6, 0.6, 0.6])
    assert fake_time.sleep_end_offsets[-1] == pytest.approx(spec.duration_seconds)
    assert all(offset <= spec.duration_seconds for offset in fake_time.sleep_end_offsets)
    assert report.summary.observed_pressure is True
    assert report.summary.recovered_after_pressure is True
    assert report.summary.recovery_latency_seconds == 3.0
    assert report.correctness.passed is True
