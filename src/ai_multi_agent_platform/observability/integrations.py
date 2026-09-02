"""Cross-cutting integration seams for observability consumers and transports."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Protocol

from ai_multi_agent_platform.contracts import HealthStatus, ProviderContract, ProviderDescriptor
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.messaging import TraceContext, TransportEnvelope

from .exporters import ObservabilityExporter
from .health import DependencyHealth, ReadinessState, ServiceHealth, aggregate_health
from .models import MetricRecord, SpanRecord, StructuredLog, TimelineEntry
from .propagation import TraceCarrier

_TRACE_BAGGAGE_STEP_ID = "ai-observability-step-id"


class TimelineReader(Protocol):
    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]: ...


class MeasurementSink(Protocol):
    """#76-facing measurement input; intentionally contains no budget semantics."""

    def ingest_metric(self, record: MetricRecord) -> None: ...


class AccountingBridgeExporter(ObservabilityExporter):
    """Forward metrics to a durable-accounting consumer while retaining telemetry ownership.

    Logs, spans and timelines only go to the configured observability exporter. Metric
    records are additionally offered to the accounting sink. The bridge does not create
    UsageRecords, budgets, costs or accounting state; those remain owned by issue #76.
    """

    def __init__(
        self,
        delegate: ObservabilityExporter,
        measurement_sink: MeasurementSink,
        *,
        strict: bool = False,
    ) -> None:
        self.delegate = delegate
        self.measurement_sink = measurement_sink
        self.strict = strict
        self.last_measurement_error: str | None = None

    def emit_log(self, record: StructuredLog) -> None:
        self.delegate.emit_log(record)

    def emit_metric(self, record: MetricRecord) -> None:
        self.delegate.emit_metric(record)
        try:
            self.measurement_sink.ingest_metric(record)
        except Exception as exc:
            self.last_measurement_error = type(exc).__name__
            if self.strict:
                raise

    def emit_span(self, record: SpanRecord) -> None:
        self.delegate.emit_span(record)

    def emit_timeline(self, record: TimelineEntry) -> None:
        self.delegate.emit_timeline(record)


@dataclass(frozen=True, slots=True)
class ProviderHealthDependency:
    provider: ProviderContract
    required: bool = True
    name: str | None = None

    @property
    def dependency_name(self) -> str:
        return self.name or self.provider.descriptor.provider_id


class AggregatedHealthProvider(ProviderContract):
    """Expose required-vs-optional dependency degradation through the existing API health seam."""

    def __init__(
        self,
        dependencies: tuple[ProviderHealthDependency, ...],
        *,
        provider_id: str = "platform-observability-health",
    ) -> None:
        self._dependencies = dependencies
        self._provider_id = provider_id
        self._status = HealthStatus.UNKNOWN
        self._service_health = ServiceHealth(alive=True, readiness=ReadinessState.READY)

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="observability-health",
            supported_operations=("health",),
            health=self._status,
            available=True,
        )

    @property
    def service_health(self) -> ServiceHealth:
        return self._service_health

    async def health(self) -> HealthStatus:
        dependencies: list[DependencyHealth] = []
        for item in self._dependencies:
            status = await item.provider.health()
            dependencies.append(
                DependencyHealth(
                    name=item.dependency_name,
                    state=_readiness_from_provider(status),
                    required=item.required,
                    detail=status.value,
                )
            )
        self._service_health = aggregate_health(tuple(dependencies))
        if not self._service_health.ready:
            self._status = HealthStatus.UNAVAILABLE
        elif self._service_health.readiness is ReadinessState.DEGRADED:
            self._status = HealthStatus.DEGRADED
        else:
            self._status = HealthStatus.HEALTHY
        return self._status


def _readiness_from_provider(status: HealthStatus) -> ReadinessState:
    if status is HealthStatus.HEALTHY:
        return ReadinessState.READY
    if status is HealthStatus.UNAVAILABLE:
        return ReadinessState.UNAVAILABLE
    return ReadinessState.DEGRADED


def inject_trace_carrier(
    envelope: TransportEnvelope,
    carrier: TraceCarrier,
) -> TransportEnvelope:
    """Attach canonical trace parentage to a replaceable #35 transport envelope."""

    baggage = dict(envelope.trace_context.baggage)
    if carrier.step_id is not None:
        baggage[_TRACE_BAGGAGE_STEP_ID] = carrier.step_id
    trace_context = TraceContext(
        trace_id=carrier.trace_id,
        span_id=carrier.parent_span_id,
        trace_flags=envelope.trace_context.trace_flags,
        tracestate=envelope.trace_context.tracestate,
        baggage=baggage,
    )
    return replace(
        envelope,
        correlation_id=carrier.correlation_id,
        causation_id=carrier.causation_id or envelope.causation_id,
        project_id=carrier.project_id or envelope.project_id,
        task_id=carrier.task_id or envelope.task_id,
        run_id=carrier.run_id or envelope.run_id,
        trace_context=trace_context,
    )


def extract_trace_carrier(envelope: TransportEnvelope) -> TraceCarrier:
    """Reconstruct a trace parent after a message crossed a process/worker boundary."""

    trace_id = envelope.trace_context.trace_id
    parent_span_id = envelope.trace_context.span_id
    if trace_id is None or not trace_id.strip():
        raise ValueError("transport envelope is missing trace_id")
    if parent_span_id is None or not parent_span_id.strip():
        raise ValueError("transport envelope is missing parent span_id")
    step_id = envelope.trace_context.baggage.get(_TRACE_BAGGAGE_STEP_ID)
    return TraceCarrier(
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        project_id=envelope.project_id,
        task_id=envelope.task_id,
        run_id=envelope.run_id,
        step_id=step_id,
    )


def timeline_entry_resource(entry: TimelineEntry) -> dict[str, JsonValue]:
    """Serialize derived telemetry for the canonical Control Plane timeline surface."""

    failure: JsonValue = None
    if entry.failure is not None:
        failure = {
            "component": entry.failure.component.value,
            "code": entry.failure.code,
            "retryable": entry.failure.retryable,
        }
    context: dict[str, JsonValue] = dict(entry.context.fields())
    attributes: dict[str, JsonValue] = dict(entry.attributes)
    identity = json.dumps(
        {
            "event_name": entry.event_name,
            "component": entry.component.value,
            "timestamp": entry.timestamp.isoformat(),
            "context": context,
            "outcome": entry.outcome.value,
            "attributes": attributes,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
    return {
        "id": f"telemetry_{digest}",
        "type": "telemetry",
        "event_name": entry.event_name,
        "component": entry.component.value,
        "timestamp": entry.timestamp.isoformat(),
        "outcome": entry.outcome.value,
        "duration_seconds": entry.duration_seconds,
        "failure": failure,
        "context": context,
        "attributes": attributes,
    }
