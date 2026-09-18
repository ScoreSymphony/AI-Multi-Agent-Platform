from __future__ import annotations

import asyncio
import time

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderContract,
    ProviderDescriptor,
)
from ai_multi_agent_platform.observability import (
    AggregatedHealthProvider,
    DependencyHealth,
    InMemoryExporter,
    MetricRecord,
    ProviderHealthDependency,
    ReadinessState,
    Telemetry,
    TimelineEntry,
    aggregate_health,
)


class _HealthProvider(ProviderContract):
    def __init__(self, statuses: list[HealthStatus]) -> None:
        self.statuses = statuses
        self.calls = 0

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="dependency-fixture",
            provider_type="test",
            health=HealthStatus.UNKNOWN,
            available=True,
        )

    async def health(self) -> HealthStatus:
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        return self.statuses[index]


class _HangingHealthProvider(_HealthProvider):
    def __init__(self) -> None:
        super().__init__([HealthStatus.UNKNOWN])
        self.cancelled = 0

    async def health(self) -> HealthStatus:
        self.calls += 1
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        return HealthStatus.HEALTHY


class _FailingHealthProvider(_HealthProvider):
    async def health(self) -> HealthStatus:
        self.calls += 1
        raise ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            "dependency temporarily unavailable",
            retryable=True,
        )


class _FailingTelemetryExporter(InMemoryExporter):
    def emit_metric(self, record: MetricRecord) -> None:
        del record
        raise RuntimeError("telemetry unavailable")

    def emit_timeline(self, record: TimelineEntry) -> None:
        del record
        raise RuntimeError("telemetry unavailable")


def test_health_states_fail_closed_for_reconciliation_and_operator_intervention() -> None:
    reconciling = aggregate_health(
        (
            DependencyHealth(
                name="persistence",
                state=ReadinessState.RECONCILING,
                required=True,
            ),
        )
    )
    assert reconciling.readiness is ReadinessState.RECONCILING
    assert not reconciling.ready

    operator_required = aggregate_health(
        (
            DependencyHealth(
                name="execution",
                state=ReadinessState.OPERATOR_INTERVENTION_REQUIRED,
                required=True,
            ),
        )
    )
    assert operator_required.readiness is ReadinessState.OPERATOR_INTERVENTION_REQUIRED
    assert not operator_required.ready


def test_optional_hanging_dependency_is_bounded_and_degraded() -> None:
    async def scenario() -> None:
        provider = _HangingHealthProvider()
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=False,
                    timeout_seconds=0.01,
                    max_retries=1,
                    backoff_seconds=0,
                ),
            )
        )
        started = time.monotonic()
        status = await health.health()
        elapsed = time.monotonic() - started

        assert status is HealthStatus.DEGRADED
        assert health.service_health.ready
        dependency = health.service_health.dependencies[0]
        assert dependency.state is ReadinessState.UNAVAILABLE
        assert dependency.error_code == ErrorCode.TIMEOUT.value
        assert dependency.attempts == 2
        assert dependency.retry_count == 1
        assert dependency.last_retry_error_code == ErrorCode.TIMEOUT.value
        assert dependency.probe_duration_seconds > 0
        assert dependency.degraded_duration_seconds is None
        assert dependency.failure_count == 1
        assert dependency.recovery_count == 0
        assert provider.calls == 2
        assert provider.cancelled == 2
        assert elapsed < 0.5

    asyncio.run(scenario())


def test_required_transient_failure_retries_only_to_configured_bound() -> None:
    async def scenario() -> None:
        provider = _FailingHealthProvider([HealthStatus.UNKNOWN])
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=True,
                    timeout_seconds=0.1,
                    max_retries=2,
                    backoff_seconds=0,
                ),
            )
        )

        assert await health.health() is HealthStatus.UNAVAILABLE
        dependency = health.service_health.dependencies[0]
        assert dependency.error_code == ErrorCode.TRANSIENT_FAILURE.value
        assert dependency.attempts == 3
        assert dependency.retry_count == 2
        assert dependency.last_retry_error_code == ErrorCode.TRANSIENT_FAILURE.value
        assert provider.calls == 3
        assert not health.service_health.ready

    asyncio.run(scenario())


def test_dependency_recovery_and_repeated_flaps_are_counted_without_extra_attempts() -> None:
    async def scenario() -> None:
        provider = _HealthProvider(
            [
                HealthStatus.UNAVAILABLE,
                HealthStatus.HEALTHY,
                HealthStatus.UNAVAILABLE,
                HealthStatus.HEALTHY,
            ]
        )
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=True,
                    max_retries=0,
                    backoff_seconds=0,
                ),
            )
        )

        observed: list[tuple[int, int]] = []
        for _ in range(4):
            await health.health()
            dependency = health.service_health.dependencies[0]
            observed.append((dependency.failure_count, dependency.recovery_count))

        assert observed == [(1, 0), (1, 1), (2, 1), (2, 2)]
        dependency = health.service_health.dependencies[0]
        assert dependency.degraded_duration_seconds is not None
        assert dependency.degraded_duration_seconds >= 0
        assert provider.calls == 4
        assert health.service_health.ready

    asyncio.run(scenario())


def test_cancellation_interrupts_health_retry_immediately() -> None:
    async def scenario() -> None:
        provider = _HangingHealthProvider()
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=True,
                    timeout_seconds=30,
                    max_retries=5,
                    backoff_seconds=1,
                ),
            )
        )
        task = asyncio.create_task(health.health())
        while provider.calls == 0:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert provider.calls == 1
        assert provider.cancelled == 1

    asyncio.run(scenario())


def test_operational_reconciliation_and_operator_blockers_are_projected_as_required() -> None:
    async def scenario() -> None:
        provider = _HealthProvider([HealthStatus.HEALTHY])
        health = AggregatedHealthProvider(
            (ProviderHealthDependency(provider, required=True, max_retries=0),)
        )

        health.set_operational_state(
            ReadinessState.RECONCILING,
            detail="startup reconciliation is in progress",
        )
        assert await health.health() is HealthStatus.UNAVAILABLE
        assert health.service_health.readiness is ReadinessState.RECONCILING
        runtime = next(
            dependency
            for dependency in health.service_health.dependencies
            if dependency.name == "platform-runtime"
        )
        assert runtime.required is True

        health.set_operational_state(
            ReadinessState.OPERATOR_INTERVENTION_REQUIRED,
            detail="canonical recovery requires an operator decision",
        )
        assert await health.health() is HealthStatus.UNAVAILABLE
        assert (
            health.service_health.readiness
            is ReadinessState.OPERATOR_INTERVENTION_REQUIRED
        )

        health.set_operational_state(None)
        assert await health.health() is HealthStatus.HEALTHY
        assert health.service_health.readiness is ReadinessState.READY

    asyncio.run(scenario())


def test_dependency_retry_recovery_and_readiness_transitions_emit_telemetry() -> None:
    async def scenario() -> None:
        exporter = InMemoryExporter()
        telemetry = Telemetry(exporter)
        provider = _HealthProvider(
            [
                HealthStatus.UNAVAILABLE,
                HealthStatus.UNAVAILABLE,
                HealthStatus.HEALTHY,
            ]
        )
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=True,
                    max_retries=1,
                    backoff_seconds=0,
                ),
            ),
            telemetry=telemetry,
        )

        assert await health.health() is HealthStatus.UNAVAILABLE
        assert await health.health() is HealthStatus.HEALTHY

        names = [entry.event_name for entry in exporter.timeline]
        assert "dependency.health.retry" in names
        assert "dependency.degradation.started" in names
        assert "dependency.degradation.recovered" in names
        assert names.count("platform.readiness.transition") == 2

        retry = next(
            entry for entry in exporter.timeline if entry.event_name == "dependency.health.retry"
        )
        assert retry.failure is not None
        assert retry.failure.code == ErrorCode.UNAVAILABLE.value
        assert retry.failure.retryable is True
        assert retry.attributes["attempt"] == 1
        assert retry.attributes["maximum_attempts"] == 2

        recovered = next(
            entry
            for entry in exporter.timeline
            if entry.event_name == "dependency.degradation.recovered"
        )
        assert recovered.duration_seconds is not None
        assert recovered.duration_seconds >= 0
        assert recovered.attributes["failure_count"] == 1
        assert recovered.attributes["recovery_count"] == 1

        metric_names = [metric.name for metric in exporter.metrics]
        assert "platform.dependency.health.retry" in metric_names
        assert "platform.dependency.degradation.duration" in metric_names

    asyncio.run(scenario())


def test_telemetry_export_failure_does_not_block_optional_dependency_isolation() -> None:
    async def scenario() -> None:
        telemetry = Telemetry(_FailingTelemetryExporter())
        provider = _HealthProvider([HealthStatus.UNAVAILABLE])
        health = AggregatedHealthProvider(
            (
                ProviderHealthDependency(
                    provider,
                    required=False,
                    max_retries=0,
                    backoff_seconds=0,
                ),
            ),
            telemetry=telemetry,
        )

        assert await health.health() is HealthStatus.DEGRADED
        assert health.service_health.ready is True
        assert health.service_health.readiness is ReadinessState.DEGRADED
        assert telemetry.last_export_error == "RuntimeError"

    asyncio.run(scenario())
