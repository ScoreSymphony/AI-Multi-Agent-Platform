"""Cross-cutting integration seams for observability consumers and transports."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Protocol

from ai_multi_agent_platform.contracts import (
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderContract,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.messaging import TraceContext, TransportEnvelope

from .exporters import ObservabilityExporter, Telemetry
from .health import DependencyHealth, ReadinessState, ServiceHealth, aggregate_health
from .models import (
    FailureClassification,
    FailureComponent,
    MetricRecord,
    SpanRecord,
    StructuredLog,
    TelemetryContext,
    TelemetryOutcome,
    TimelineEntry,
)
from .propagation import TraceCarrier

_TRACE_PREFIX = "ai-observability-"


class TimelineReader(Protocol):
    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]: ...


class MeasurementSink(Protocol):
    """-facing measurement input; intentionally contains no budget semantics."""

    def ingest_metric(self, record: MetricRecord) -> None: ...


class AccountingBridgeExporter(ObservabilityExporter):
    """Forward metrics to a durable-accounting consumer while retaining telemetry ownership.

    Logs, spans and timelines only go to the configured observability exporter. Metric
    records are additionally offered to the accounting sink. The bridge does not create
    UsageRecords, budgets, costs or accounting state; those remain owned by the owning subsystem.
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
        # error-boundary: allow-broad-catch=boundary observability owner boundary
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
    timeout_seconds: float = 2.0
    max_retries: int = 1
    backoff_seconds: float = 0.05
    operator_action: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("health probe timeout_seconds must be positive")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("health probe max_retries must be between 0 and 5")
        if self.backoff_seconds < 0:
            raise ValueError("health probe backoff_seconds must not be negative")

    @property
    def dependency_name(self) -> str:
        return self.name or self.provider.descriptor.provider_id


_RETRYABLE_HEALTH_ERROR_CODES = frozenset(
    {
        ErrorCode.UNAVAILABLE,
        ErrorCode.TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.RESOURCE_EXHAUSTED,
        ErrorCode.TRANSIENT_FAILURE,
    }
)


@dataclass(frozen=True, slots=True)
class _DependencyProbeResult:
    state: ReadinessState
    detail: str | None
    error_code: str | None
    retryable: bool
    attempts: int
    last_retry_error_code: str | None


class AggregatedHealthProvider(ProviderContract):
    """Expose bounded required-vs-optional dependency health through the existing API seam."""

    def __init__(
        self,
        dependencies: tuple[ProviderHealthDependency, ...],
        *,
        provider_id: str = "platform-observability-health",
        telemetry: Telemetry | None = None,
    ) -> None:
        self._dependencies = dependencies
        self._provider_id = provider_id
        self._telemetry = telemetry
        self._status = HealthStatus.UNKNOWN
        self._service_health = ServiceHealth(alive=True, readiness=ReadinessState.READY)
        self._last_states: dict[str, ReadinessState] = {}
        self._failure_counts: dict[str, int] = {}
        self._recovery_counts: dict[str, int] = {}
        self._failure_started_at: dict[str, float] = {}
        self._probe_lock = asyncio.Lock()
        self._operational_state: ReadinessState | None = None
        self._operational_detail: str | None = None
        self._operational_action: str | None = None
        self._last_readiness_state: ReadinessState | None = None
        self._health_diagnostics: tuple[dict[str, JsonValue], ...] = ()

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

    @property
    def health_diagnostics(self) -> tuple[dict[str, JsonValue], ...]:
        return self._health_diagnostics

    @property
    def health_timeout_seconds(self) -> float:
        """Upper bound used by the outer Control Plane health probe."""

        return sum(
            item.timeout_seconds * (item.max_retries + 1)
            + item.backoff_seconds * sum(range(1, item.max_retries + 1))
            for item in self._dependencies
        ) + 0.1

    def set_operational_state(
        self,
        state: ReadinessState | None,
        *,
        detail: str | None = None,
        operator_action: str | None = None,
    ) -> None:
        """Project platform-owned recovery/drain state without taking lifecycle ownership."""

        if state not in {
            None,
            ReadinessState.RECONCILING,
            ReadinessState.OPERATOR_INTERVENTION_REQUIRED,
            ReadinessState.DRAINING,
        }:
            raise ValueError(
                "operational state must be reconciling, operator-required, draining or None"
            )
        previous = self._operational_state
        self._operational_state = state
        self._operational_detail = detail
        self._operational_action = operator_action
        if previous is not state:
            self._observe_operational_state(previous, state)

    async def health(self) -> HealthStatus:
        async with self._probe_lock:
            dependencies: list[DependencyHealth] = []
            diagnostics: list[dict[str, JsonValue]] = []
            for item in self._dependencies:
                dependency = await self._probe_dependency(item)
                dependencies.append(dependency)
                provider_diagnostics = getattr(item.provider, "health_diagnostics", ())
                if dependency.state is not ReadinessState.READY or provider_diagnostics:
                    diagnostic: dict[str, JsonValue] = {
                        "dependency": item.dependency_name,
                        "provider_id": item.provider.descriptor.provider_id,
                        "status": dependency.detail or dependency.state.value,
                        "required": item.required,
                    }
                    if provider_diagnostics:
                        diagnostic["diagnostics"] = list(provider_diagnostics)
                    diagnostics.append(diagnostic)
            self._health_diagnostics = tuple(diagnostics)
            if self._operational_state is not None:
                dependencies.append(
                    DependencyHealth(
                        name="platform-runtime",
                        state=self._operational_state,
                        required=True,
                        detail=self._operational_detail,
                        operator_action=self._operational_action,
                    )
                )
            self._service_health = aggregate_health(tuple(dependencies))
            self._observe_readiness_transition()
            if not self._service_health.ready:
                self._status = HealthStatus.UNAVAILABLE
            elif self._service_health.readiness is ReadinessState.DEGRADED:
                self._status = HealthStatus.DEGRADED
            else:
                self._status = HealthStatus.HEALTHY
            return self._status

    async def _probe_dependency(self, item: ProviderHealthDependency) -> DependencyHealth:
        probe_started = time.monotonic()
        result = await self._execute_dependency_probe(item)
        name = item.dependency_name
        previous, degraded_duration_seconds = self._record_dependency_transition(
            name,
            result.state,
        )
        currently_impaired = result.state is not ReadinessState.READY
        operator_action = item.operator_action
        if currently_impaired and operator_action is None:
            operator_action = self._default_operator_action(item.required)

        dependency = DependencyHealth(
            name=name,
            state=result.state,
            required=item.required,
            detail=result.detail,
            error_code=result.error_code,
            attempts=result.attempts,
            retry_count=max(0, result.attempts - 1),
            last_retry_error_code=result.last_retry_error_code,
            probe_duration_seconds=time.monotonic() - probe_started,
            degraded_duration_seconds=degraded_duration_seconds,
            failure_count=self._failure_counts.get(name, 0),
            recovery_count=self._recovery_counts.get(name, 0),
            operator_action=operator_action,
        )
        self._observe_dependency_probe(
            dependency,
            previous=previous,
            retryable=result.retryable,
        )
        return dependency

    async def _execute_dependency_probe(
        self,
        item: ProviderHealthDependency,
    ) -> _DependencyProbeResult:
        attempts = 0
        state = ReadinessState.UNAVAILABLE
        detail: str | None = None
        error_code: str | None = None
        last_retry_error_code: str | None = None
        retryable = False
        maximum_attempts = item.max_retries + 1

        while attempts < maximum_attempts:
            attempts += 1
            retryable = False
            try:
                status = await asyncio.wait_for(
                    item.provider.health(),
                    timeout=item.timeout_seconds,
                )
                state = _readiness_from_provider(status)
                detail = status.value
                error_code = (
                    None
                    if state is not ReadinessState.UNAVAILABLE
                    else ErrorCode.UNAVAILABLE.value
                )
                retryable = state is ReadinessState.UNAVAILABLE
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                state = ReadinessState.UNAVAILABLE
                detail = "health probe timed out"
                error_code = ErrorCode.TIMEOUT.value
                retryable = True
            except ContractError as exc:
                state = ReadinessState.UNAVAILABLE
                detail = exc.message
                error_code = exc.code.value
                retryable = exc.retryable or exc.code in _RETRYABLE_HEALTH_ERROR_CODES
            # error-boundary: allow-broad-catch=boundary provider health probe normalization
            except Exception:
                state = ReadinessState.UNAVAILABLE
                detail = "provider health probe failed"
                error_code = ErrorCode.BACKEND_ERROR.value
                retryable = False

            if state is not ReadinessState.UNAVAILABLE:
                break
            if attempts >= maximum_attempts or not retryable:
                break
            last_retry_error_code = error_code
            self._observe_retry(
                item,
                error_code=error_code,
                attempt=attempts,
                maximum_attempts=maximum_attempts,
            )
            if item.backoff_seconds > 0:
                await asyncio.sleep(item.backoff_seconds * attempts)

        return _DependencyProbeResult(
            state=state,
            detail=detail,
            error_code=error_code,
            retryable=retryable,
            attempts=attempts,
            last_retry_error_code=last_retry_error_code,
        )

    def _record_dependency_transition(
        self,
        name: str,
        state: ReadinessState,
    ) -> tuple[ReadinessState | None, float | None]:
        previous = self._last_states.get(name)
        previously_impaired = previous is not None and previous is not ReadinessState.READY
        currently_impaired = state is not ReadinessState.READY
        transition_time = time.monotonic()
        degraded_duration_seconds: float | None = None
        if currently_impaired and not previously_impaired:
            self._failure_counts[name] = self._failure_counts.get(name, 0) + 1
            self._failure_started_at[name] = transition_time
        elif not currently_impaired and previously_impaired:
            self._recovery_counts[name] = self._recovery_counts.get(name, 0) + 1
            failure_started = self._failure_started_at.pop(name, None)
            if failure_started is not None:
                degraded_duration_seconds = transition_time - failure_started
        elif currently_impaired:
            failure_started = self._failure_started_at.get(name)
            if failure_started is not None:
                degraded_duration_seconds = transition_time - failure_started
        self._last_states[name] = state
        return previous, degraded_duration_seconds

    @staticmethod
    def _default_operator_action(required: bool) -> str:
        if required:
            return (
                "restore the required dependency and rerun platform doctor; "
                "do not mutate canonical lifecycle state directly"
            )
        return (
            "restore or disable the optional dependency; unrelated canonical "
            "operations may continue"
        )

    def _observe_retry(
        self,
        item: ProviderHealthDependency,
        *,
        error_code: str | None,
        attempt: int,
        maximum_attempts: int,
    ) -> None:
        if self._telemetry is None:
            return
        code = error_code or ErrorCode.UNAVAILABLE.value
        context = TelemetryContext(provider_id=item.dependency_name)
        attributes: dict[str, JsonValue] = {
            "dependency": item.dependency_name,
            "required": item.required,
            "attempt": attempt,
            "maximum_attempts": maximum_attempts,
        }
        failure = FailureClassification(
            component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
            code=code,
            retryable=True,
        )
        self._telemetry.timeline(
            event_name="dependency.health.retry",
            component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
            context=context,
            outcome=TelemetryOutcome.FAILED,
            failure=failure,
            attributes=attributes,
        )
        self._telemetry.metric(
            "platform.dependency.health.retry",
            1.0,
            context=context,
            attributes=attributes,
        )

    def _observe_dependency_probe(
        self,
        dependency: DependencyHealth,
        *,
        previous: ReadinessState | None,
        retryable: bool,
    ) -> None:
        if self._telemetry is None:
            return
        context = TelemetryContext(provider_id=dependency.name)
        failure = (
            FailureClassification(
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                code=dependency.error_code,
                retryable=retryable,
            )
            if dependency.error_code is not None
            else None
        )
        attributes: dict[str, JsonValue] = {
            "dependency": dependency.name,
            "state": dependency.state.value,
            "required": dependency.required,
            "attempts": dependency.attempts,
            "retry_count": dependency.retry_count,
            "failure_count": dependency.failure_count,
            "recovery_count": dependency.recovery_count,
        }
        if dependency.last_retry_error_code is not None:
            attributes["last_retry_error_code"] = dependency.last_retry_error_code

        outcome = (
            TelemetryOutcome.SUCCEEDED
            if dependency.state is ReadinessState.READY
            else TelemetryOutcome.FAILED
        )
        self._telemetry.timeline(
            event_name="dependency.health.probe",
            component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
            context=context,
            outcome=outcome,
            duration_seconds=dependency.probe_duration_seconds,
            failure=failure,
            attributes=attributes,
        )

        previously_impaired = previous is not None and previous is not ReadinessState.READY
        currently_impaired = dependency.state is not ReadinessState.READY
        if currently_impaired and not previously_impaired:
            self._telemetry.timeline(
                event_name="dependency.degradation.started",
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                context=context,
                outcome=TelemetryOutcome.FAILED,
                failure=failure,
                attributes=attributes,
            )
        elif not currently_impaired and previously_impaired:
            self._telemetry.timeline(
                event_name="dependency.degradation.recovered",
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                context=context,
                outcome=TelemetryOutcome.SUCCEEDED,
                duration_seconds=dependency.degraded_duration_seconds,
                attributes=attributes,
            )
            if dependency.degraded_duration_seconds is not None:
                self._telemetry.metric(
                    "platform.dependency.degradation.duration",
                    dependency.degraded_duration_seconds,
                    context=context,
                    unit="seconds",
                    attributes=attributes,
                )

    def _observe_readiness_transition(self) -> None:
        current = self._service_health.readiness
        previous = self._last_readiness_state
        if current is previous:
            return
        self._last_readiness_state = current
        if self._telemetry is None:
            return
        attributes: dict[str, JsonValue] = {
            "previous_state": previous.value if previous is not None else "unknown",
            "state": current.value,
            "alive": self._service_health.alive,
            "ready": self._service_health.ready,
        }
        self._telemetry.timeline(
            event_name="platform.readiness.transition",
            component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
            context=TelemetryContext(provider_id=self._provider_id),
            outcome=(
                TelemetryOutcome.SUCCEEDED
                if self._service_health.ready
                else TelemetryOutcome.FAILED
            ),
            attributes=attributes,
        )

    def _observe_operational_state(
        self,
        previous: ReadinessState | None,
        current: ReadinessState | None,
    ) -> None:
        if self._telemetry is None:
            return
        attributes: dict[str, JsonValue] = {
            "previous_state": previous.value if previous is not None else "ready",
            "state": current.value if current is not None else "ready",
        }
        self._telemetry.timeline(
            event_name="platform.operational_state.changed",
            component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
            context=TelemetryContext(provider_id=self._provider_id),
            outcome=(
                TelemetryOutcome.SUCCEEDED
                if current is None
                else TelemetryOutcome.FAILED
            ),
            attributes=attributes,
        )


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
    """Attach trace parentage and canonical observability context to a  envelope."""

    baggage = dict(envelope.trace_context.baggage)
    baggage.update(carrier.to_mapping())
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
    """Reconstruct the full canonical trace context after a transport boundary."""

    trace_id = envelope.trace_context.trace_id
    parent_span_id = envelope.trace_context.span_id
    if trace_id is None or not trace_id.strip():
        raise ValueError("transport envelope is missing trace_id")
    if parent_span_id is None or not parent_span_id.strip():
        raise ValueError("transport envelope is missing parent span_id")

    values = dict(envelope.trace_context.baggage)
    values[f"{_TRACE_PREFIX}trace-id"] = trace_id
    values[f"{_TRACE_PREFIX}parent-span-id"] = parent_span_id
    values[f"{_TRACE_PREFIX}correlation-id"] = envelope.correlation_id
    for key, value in (
        ("causation-id", envelope.causation_id),
        ("project-id", envelope.project_id),
        ("task-id", envelope.task_id),
        ("run-id", envelope.run_id),
    ):
        if value is not None:
            values[f"{_TRACE_PREFIX}{key}"] = value
    return TraceCarrier.from_mapping(values)


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
