from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    ControlPlaneRole,
    HighAvailabilityTelemetry,
    InMemoryCoordinationProvider,
    StaleFencingToken,
)
from ai_multi_agent_platform.observability import (
    InMemoryExporter,
    MetricRecord,
    ObservabilityExporter,
    SpanRecord,
    StructuredLog,
    Telemetry,
    TimelineEntry,
)

NOW = datetime(2026, 9, 6, 3, 0, tzinfo=UTC)
LEASE_TTL = timedelta(seconds=10)


class MutableClock:
    def __init__(self, value: datetime = NOW) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class StepMonotonic:
    def __init__(self, *, step: float = 0.25) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


class ExplodingExporter(ObservabilityExporter):
    def emit_log(self, record: StructuredLog) -> None:
        del record
        raise RuntimeError("telemetry unavailable")

    def emit_metric(self, record: MetricRecord) -> None:
        del record
        raise RuntimeError("telemetry unavailable")

    def emit_span(self, record: SpanRecord) -> None:
        del record
        raise RuntimeError("telemetry unavailable")

    def emit_timeline(self, record: TimelineEntry) -> None:
        del record
        raise RuntimeError("telemetry unavailable")


def _observed_service(
    *,
    instance_id: str,
    coordinator: InMemoryCoordinationProvider,
    clock: MutableClock,
    exporter: InMemoryExporter,
) -> ControlPlaneFailoverService:
    return ControlPlaneFailoverService(
        instance_id=instance_id,
        mode=AvailabilityMode.ACTIVE_PASSIVE,
        coordinator=coordinator,
        lease_ttl=LEASE_TTL,
        telemetry=HighAvailabilityTelemetry(Telemetry(exporter)),
        observation_clock=clock,
        monotonic=StepMonotonic(),
    )


def test_promotion_and_renewal_emit_backend_neutral_ha_telemetry() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        exporter = InMemoryExporter()
        service = _observed_service(
            instance_id="control-a",
            coordinator=coordinator,
            clock=clock,
            exporter=exporter,
        )

        assert await service.start(reason="observability-test") is True
        status = await service.status()
        assert status.role is ControlPlaneRole.ACTIVE
        assert status.epoch == 1
        assert status.lease_age_seconds == 0.0
        assert status.last_renewed_at is None
        assert status.last_error_code is None
        assert status.reconciliation_in_progress is False

        event_names = {entry.event_name for entry in exporter.timeline}
        assert "control_plane.ha.promotion_started" in event_names
        assert "control_plane.ha.role_changed" in event_names
        assert "control_plane.ha.promotion_completed" in event_names
        metric_names = {metric.name for metric in exporter.metrics}
        assert "platform.control_plane.ha.promotion_attempts" in metric_names
        assert "platform.control_plane.ha.promotions" in metric_names
        assert "platform.control_plane.ha.promotion_duration_seconds" in metric_names

        completed = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "control_plane.ha.promotion_completed"
        )
        assert completed.attributes["instance_id"] == "control-a"
        assert completed.attributes["mode"] == AvailabilityMode.ACTIVE_PASSIVE.value
        assert completed.attributes["role"] == ControlPlaneRole.ACTIVE.value
        assert completed.attributes["epoch"] == 1
        assert completed.attributes["reason"] == "observability-test"
        assert completed.duration_seconds == pytest.approx(0.25)

        clock.advance(timedelta(seconds=2))
        renewed = await service.renew()
        assert renewed is not None
        assert renewed.token.epoch == 1
        renewed_at = clock.value

        clock.advance(timedelta(seconds=3))
        status = await service.status()
        assert status.last_renewed_at == renewed_at
        assert status.seconds_since_last_renewal == pytest.approx(3.0)
        assert status.lease_age_seconds == pytest.approx(5.0)
        assert any(
            metric.name == "platform.control_plane.ha.lease_renewals" for metric in exporter.metrics
        )

    asyncio.run(scenario())


def test_stale_leader_records_fencing_rejection_and_last_error() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first_exporter = InMemoryExporter()
        second_exporter = InMemoryExporter()
        first = _observed_service(
            instance_id="control-a",
            coordinator=coordinator,
            clock=clock,
            exporter=first_exporter,
        )
        second = _observed_service(
            instance_id="control-b",
            coordinator=coordinator,
            clock=clock,
            exporter=second_exporter,
        )

        assert await first.start(reason="initial") is True
        clock.advance(LEASE_TTL + timedelta(milliseconds=1))
        assert await second.try_promote(reason="failover") is True

        with pytest.raises(StaleFencingToken):
            await first.require_authority()

        status = await first.status()
        assert status.role is ControlPlaneRole.FENCED
        assert status.leader_instance_id == "control-b"
        assert status.last_error_code == "stale_fencing_token"
        assert any(
            log.event_name == "control_plane.ha.authority_rejected"
            and log.attributes["failure_code"] == "stale_fencing_token"
            for log in first_exporter.logs
        )
        assert any(
            metric.name == "platform.control_plane.ha.authority_rejections"
            for metric in first_exporter.metrics
        )

    asyncio.run(scenario())


def test_telemetry_export_failure_cannot_change_leadership_semantics() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        service = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=LEASE_TTL,
            telemetry=HighAvailabilityTelemetry(Telemetry(ExplodingExporter())),
            observation_clock=clock,
            monotonic=StepMonotonic(),
        )

        assert await service.start(reason="telemetry-failure") is True
        assert service.role is ControlPlaneRole.ACTIVE
        clock.advance(timedelta(seconds=1))
        renewed = await service.renew()
        assert renewed is not None
        grant = await service.require_authority()
        assert grant.fencing_token is not None
        assert grant.fencing_token.epoch == 1

    asyncio.run(scenario())
