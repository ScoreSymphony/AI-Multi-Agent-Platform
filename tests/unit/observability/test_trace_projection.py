from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry
from ai_multi_agent_platform.observability.models import (
    FailureClassification,
    FailureComponent,
    SpanRecord,
    TelemetryContext,
    TelemetryOutcome,
    TimelineEntry,
)
from ai_multi_agent_platform.observability.trace import (
    TraceFilter,
    TraceProjection,
    TraceUsageRecord,
    trace_node_resource,
)


class _UsageReader:
    def __init__(self, records: tuple[TraceUsageRecord, ...]) -> None:
        self.records = records

    def query_trace_usage(self, *, task_id: str) -> tuple[TraceUsageRecord, ...]:
        return tuple(record for record in self.records if record.scope.get("task_id") == task_id)


class _TimelineReader:
    def __init__(self, entries: tuple[TimelineEntry, ...]) -> None:
        self.entries = entries

    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        return tuple(
            entry
            for entry in self.entries
            if (task_id is None or entry.context.task_id == task_id)
            and (run_id is None or entry.context.run_id == run_id)
            and (correlation_id is None or entry.context.correlation_id == correlation_id)
        )


class _SpanReader:
    def __init__(self, spans: tuple[SpanRecord, ...]) -> None:
        self.spans = spans

    def query_spans(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[SpanRecord, ...]:
        return tuple(
            span
            for span in self.spans
            if (task_id is None or span.context.task_id == task_id)
            and (run_id is None or span.context.run_id == run_id)
            and (trace_id is None or span.trace_id == trace_id)
            and (correlation_id is None or span.context.correlation_id == correlation_id)
        )


def _span(
    name: str,
    span_id: str,
    start: datetime,
    end: datetime,
    context: TelemetryContext,
    *,
    parent: str | None = None,
    failure: FailureClassification | None = None,
    attributes: dict[str, object] | None = None,
) -> SpanRecord:
    return SpanRecord(
        name=name,
        trace_id="trace_901",
        span_id=span_id,
        parent_span_id=parent,
        context=context,
        started_at=start,
        finished_at=end,
        duration_seconds=(end - start).total_seconds(),
        outcome=TelemetryOutcome.FAILED if failure else TelemetryOutcome.SUCCEEDED,
        failure=failure,
        attributes=attributes or {},  # type: ignore[arg-type]
    )


def test_trace_projection_preserves_hierarchy_parallelism_events_and_usage() -> None:
    started = datetime(2026, 9, 15, 18, 0, tzinfo=UTC)
    task_id = "task_901"
    run_id = "run_901"
    root_context = TelemetryContext(task_id=task_id, run_id=run_id, correlation_id=task_id)
    research_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_research",
        agent_id="agent_research",
        correlation_id=task_id,
    )
    coding_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_code",
        agent_id="agent_code",
        correlation_id=task_id,
    )
    model_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_code",
        agent_id="agent_code",
        model_config_id="model_config_local",
        model_provider_id="provider_local",
        correlation_id=task_id,
    )
    spans = (
        _span("run.lifecycle", "span_root", started, started + timedelta(seconds=10), root_context),
        _span(
            "agent.run",
            "span_research",
            started + timedelta(seconds=1),
            started + timedelta(seconds=6),
            research_context,
            parent="span_root",
        ),
        _span(
            "agent.run",
            "span_code",
            started + timedelta(seconds=2),
            started + timedelta(seconds=8),
            coding_context,
            parent="span_root",
            attributes={"artifact_id": "artifact_901"},
        ),
        _span(
            "model.generate",
            "span_model",
            started + timedelta(seconds=3),
            started + timedelta(seconds=4),
            model_context,
            parent="span_code",
        ),
    )
    timeline = (
        TimelineEntry(
            event_name="verification.completed",
            component=FailureComponent.VERIFICATION,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                verification_id="verification_901",
                correlation_id=task_id,
            ),
            timestamp=started + timedelta(seconds=9),
            outcome=TelemetryOutcome.SUCCEEDED,
        ),
    )
    usage = (
        TraceUsageRecord(
            id="usage_model",
            metric_type="model.tokens.total",
            unit="tokens",
            quality="reported",
            source="provider",
            timestamp=started + timedelta(seconds=4),
            scope={
                "task_id": task_id,
                "run_id": run_id,
                "model_config_id": "model_config_local",
            },
            quantity=321,
        ),
        TraceUsageRecord(
            id="usage_task",
            metric_type="external.cost.amount",
            unit="EUR",
            quality="estimated",
            source="operator",
            timestamp=started + timedelta(seconds=10),
            scope={"task_id": task_id},
            quantity=0.12,
            cost_amount=0.12,
            currency="EUR",
        ),
    )
    projection = TraceProjection(
        spans=_SpanReader(spans),
        timeline=_TimelineReader(timeline),
        usage=_UsageReader(usage),
    )

    snapshot = asyncio.run(projection.query(task_id=task_id))
    nodes = {node.id: node for node in snapshot.nodes}

    assert snapshot.telemetry_state == "available"
    assert snapshot.missing_sources == ()
    assert nodes["span_code"].parent_id == "span_root"
    assert nodes["span_model"].parent_id == "span_code"
    assert nodes["span_research"].parallel_with == ("span_code",)
    assert nodes["span_code"].parallel_with == ("span_research",)
    assert nodes["span_model"].usage[0].quantity == 321
    assert snapshot.task_usage[0].quality == "estimated"
    assert snapshot.task_usage[0].cost_amount == 0.12
    assert any(node.name == "verification.completed" for node in snapshot.nodes)
    assert any(link["id"] == "artifact_901" for link in nodes["span_code"].resources)

    filtered = asyncio.run(
        projection.query(task_id=task_id, trace_filter=TraceFilter(agent_id="agent_code"))
    )
    assert {node.id for node in filtered.nodes} == {"span_code", "span_model"}


def test_trace_projection_preserves_failure_and_missing_accounting_state() -> None:
    started = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
    failure = FailureClassification(
        component=FailureComponent.EXECUTION,
        code="worker_retry_exhausted",
        retryable=True,
    )
    span = _span(
        "worker.dispatch",
        "span_failure",
        started,
        started + timedelta(seconds=1),
        TelemetryContext(task_id="task_failure", run_id="run_failure"),
        failure=failure,
    )
    projection = TraceProjection(spans=_SpanReader((span,)), timeline=None, usage=None)

    snapshot = asyncio.run(projection.query(task_id="task_failure"))
    resource = trace_node_resource(snapshot.nodes[0])

    assert snapshot.telemetry_state == "degraded"
    assert snapshot.missing_sources == ("timeline", "usage")
    assert resource["failure"] == {
        "component": "execution",
        "code": "worker_retry_exhausted",
        "retryable": True,
    }


def test_trace_projection_only_exposes_redacted_exporter_attributes() -> None:
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    handle = telemetry.start_span(
        "tool.invoke",
        context=TelemetryContext(task_id="task_redacted", capability_id="capability_shell"),
    )
    telemetry.finish_span(
        handle,
        outcome=TelemetryOutcome.SUCCEEDED,
        attributes={"secret": "must-not-leak", "result_id": "result_safe"},
    )
    projection = TraceProjection(spans=exporter, timeline=exporter)

    snapshot = asyncio.run(projection.query(task_id="task_redacted"))
    serialized = repr(trace_node_resource(snapshot.nodes[0]))

    assert "must-not-leak" not in serialized
    assert "[REDACTED]" in serialized
    assert "result_safe" in serialized
