"""Replaceable observability exporter boundary and local reference implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    CaptureKind,
    CapturePolicy,
    FailureClassification,
    FailureComponent,
    MetricRecord,
    SpanRecord,
    StructuredLog,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
    TimelineEntry,
    utc_now,
)


class ObservabilityExporter(ABC):
    """Backend-neutral sink. Implementations may buffer or forward records elsewhere."""

    @abstractmethod
    def emit_log(self, record: StructuredLog) -> None: ...

    @abstractmethod
    def emit_metric(self, record: MetricRecord) -> None: ...

    @abstractmethod
    def emit_span(self, record: SpanRecord) -> None: ...

    @abstractmethod
    def emit_timeline(self, record: TimelineEntry) -> None: ...


class NoOpExporter(ObservabilityExporter):
    """Default exporter for installations where telemetry export is disabled."""

    def emit_log(self, record: StructuredLog) -> None:
        del record

    def emit_metric(self, record: MetricRecord) -> None:
        del record

    def emit_span(self, record: SpanRecord) -> None:
        del record

    def emit_timeline(self, record: TimelineEntry) -> None:
        del record


class InMemoryExporter(ObservabilityExporter):
    """Deterministic local/reference exporter used by tests and API-ready queries."""

    def __init__(self) -> None:
        self.logs: list[StructuredLog] = []
        self.metrics: list[MetricRecord] = []
        self.spans: list[SpanRecord] = []
        self.timeline: list[TimelineEntry] = []
        self._lock = Lock()

    def emit_log(self, record: StructuredLog) -> None:
        with self._lock:
            self.logs.append(record)

    def emit_metric(self, record: MetricRecord) -> None:
        with self._lock:
            self.metrics.append(record)

    def emit_span(self, record: SpanRecord) -> None:
        with self._lock:
            self.spans.append(record)

    def emit_timeline(self, record: TimelineEntry) -> None:
        with self._lock:
            self.timeline.append(record)

    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        with self._lock:
            entries = tuple(self.timeline)
        return tuple(
            sorted(
                (
                    entry
                    for entry in entries
                    if (task_id is None or entry.context.task_id == task_id)
                    and (run_id is None or entry.context.run_id == run_id)
                    and (
                        correlation_id is None
                        or entry.context.correlation_id == correlation_id
                    )
                ),
                key=lambda entry: entry.timestamp,
            )
        )


@dataclass(frozen=True, slots=True)
class SpanHandle:
    name: str
    trace_id: str
    span_id: str
    context: TelemetryContext
    started_at: datetime
    parent_span_id: str | None = None


class Telemetry:
    """Small instrumentation facade shared by kernel, executors and later domains."""

    def __init__(
        self,
        exporter: ObservabilityExporter | None = None,
        *,
        capture_policy: CapturePolicy | None = None,
    ) -> None:
        self.exporter = exporter or NoOpExporter()
        self.capture_policy = capture_policy or CapturePolicy()
        self._anchors: dict[tuple[str, str], SpanHandle] = {}
        self._lock = Lock()

    def log(
        self,
        *,
        severity: TelemetrySeverity,
        component: FailureComponent,
        event_name: str,
        context: TelemetryContext,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        failure: FailureClassification | None = None,
        duration_seconds: float | None = None,
        attributes: dict[str, JsonValue] | None = None,
        capture_kind: CaptureKind = CaptureKind.GENERIC,
    ) -> None:
        safe = self.capture_policy.redact(attributes or {}, kind=capture_kind)
        self.exporter.emit_log(
            StructuredLog(
                severity=severity,
                component=component,
                event_name=event_name,
                context=context,
                outcome=outcome,
                failure=failure,
                duration_seconds=duration_seconds,
                attributes=safe,
            )
        )

    def metric(
        self,
        name: str,
        value: float,
        *,
        context: TelemetryContext,
        unit: str = "count",
        attributes: dict[str, JsonValue] | None = None,
    ) -> None:
        safe = self.capture_policy.redact(attributes or {})
        self.exporter.emit_metric(
            MetricRecord(
                name=name,
                value=value,
                context=context,
                unit=unit,
                attributes=safe,
            )
        )

    def timeline(
        self,
        *,
        event_name: str,
        component: FailureComponent,
        context: TelemetryContext,
        timestamp: datetime | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        duration_seconds: float | None = None,
        failure: FailureClassification | None = None,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None:
        safe = self.capture_policy.redact(attributes or {})
        self.exporter.emit_timeline(
            TimelineEntry(
                event_name=event_name,
                component=component,
                context=context,
                timestamp=timestamp or utc_now(),
                outcome=outcome,
                duration_seconds=duration_seconds,
                failure=failure,
                attributes=safe,
            )
        )

    def start_span(
        self,
        name: str,
        *,
        context: TelemetryContext,
        parent: SpanHandle | None = None,
        trace_id: str | None = None,
    ) -> SpanHandle:
        if parent is not None and trace_id is not None and parent.trace_id != trace_id:
            raise ValueError("explicit trace_id must match parent trace")
        return SpanHandle(
            name=name,
            trace_id=(parent.trace_id if parent is not None else trace_id) or self.new_trace_id(),
            span_id=self.new_span_id(),
            context=context,
            started_at=utc_now(),
            parent_span_id=parent.span_id if parent is not None else None,
        )

    def finish_span(
        self,
        handle: SpanHandle,
        *,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        failure: FailureClassification | None = None,
        attributes: dict[str, JsonValue] | None = None,
    ) -> SpanRecord:
        finished_at = utc_now()
        duration = max(0.0, (finished_at - handle.started_at).total_seconds())
        safe = self.capture_policy.redact(attributes or {})
        record = SpanRecord(
            name=handle.name,
            trace_id=handle.trace_id,
            span_id=handle.span_id,
            parent_span_id=handle.parent_span_id,
            context=handle.context,
            started_at=handle.started_at,
            finished_at=finished_at,
            duration_seconds=duration,
            outcome=outcome,
            failure=failure,
            attributes=safe,
        )
        self.exporter.emit_span(record)
        return record

    def set_anchor(self, scope: str, identifier: str, handle: SpanHandle) -> None:
        if not scope.strip() or not identifier.strip():
            raise ValueError("anchor scope and identifier must not be blank")
        with self._lock:
            self._anchors[(scope, identifier)] = handle

    def get_anchor(self, scope: str, identifier: str) -> SpanHandle | None:
        with self._lock:
            return self._anchors.get((scope, identifier))

    def pop_anchor(self, scope: str, identifier: str) -> SpanHandle | None:
        with self._lock:
            return self._anchors.pop((scope, identifier), None)

    @staticmethod
    def new_trace_id() -> str:
        return f"trace_{uuid4()}"

    @staticmethod
    def new_span_id() -> str:
        return f"span_{uuid4()}"
