from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import Event, new_id
from ai_multi_agent_platform.observability import (
    FailureComponent,
    InMemoryExporter,
    ObservabilityEventProvider,
    SpanLink,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TraceCarrier,
    TraceHierarchy,
)


def test_second_run_created_for_same_task_emits_one_retry_metric() -> None:
    task_id = new_id("task")
    run_one = new_id("run")
    run_two = new_id("run")
    exporter = InMemoryExporter()
    provider = ObservabilityEventProvider(Telemetry(exporter))

    async def scenario() -> None:
        await provider.publish(
            Event(
                event_type="task.created",
                subject_type="task",
                subject_id=task_id,
                correlation_id=task_id,
            )
        )
        await provider.publish(
            Event(
                event_type="run.created",
                subject_type="run",
                subject_id=run_one,
                correlation_id=task_id,
                payload={"task_id": task_id},
            )
        )
        second = Event(
            event_type="run.created",
            subject_type="run",
            subject_id=run_two,
            correlation_id=task_id,
            payload={"task_id": task_id},
        )
        await provider.publish(second)
        await provider.publish(second)

    asyncio.run(scenario())
    retries = [metric for metric in exporter.metrics if metric.name == "platform.run.retries"]
    assert len(retries) == 1
    assert retries[0].value == 1.0
    assert retries[0].context.task_id == task_id
    assert retries[0].context.run_id == run_two
    assert retries[0].attributes["attempt"] == 2


def test_detached_async_work_uses_links_without_false_parentage() -> None:
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    hierarchy = TraceHierarchy(telemetry)
    task_one = new_id("task")
    task_two = new_id("task")
    source_one = telemetry.start_span(
        "source.one",
        context=TelemetryContext(task_id=task_one, correlation_id=task_one),
    )
    source_two = telemetry.start_span(
        "source.two",
        context=TelemetryContext(task_id=task_two, correlation_id=task_two),
    )
    carriers = (TraceCarrier.from_span(source_one), TraceCarrier.from_span(source_two))

    async def scenario() -> None:
        async def operation() -> None:
            return None

        await hierarchy.observe_linked(
            carriers=carriers,
            span_name="async.join",
            metric_prefix="platform.async.join",
            event_prefix="async.join",
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(correlation_id="fan-in-correlation"),
            operation=operation,
        )

    asyncio.run(scenario())
    joined = next(span for span in exporter.spans if span.name == "async.join")
    assert joined.parent_span_id is None
    assert {(link.trace_id, link.span_id) for link in joined.links} == {
        (source_one.trace_id, source_one.span_id),
        (source_two.trace_id, source_two.span_id),
    }
    assert all(link.attributes["relation"] == "async_link" for link in joined.links)
    assert joined.outcome is TelemetryOutcome.SUCCEEDED


def test_span_link_attributes_follow_capture_redaction_policy() -> None:
    exporter = InMemoryExporter()
    telemetry = Telemetry(exporter)
    source = telemetry.start_span(
        "source",
        context=TelemetryContext(correlation_id="source-correlation"),
    )
    linked = telemetry.start_span(
        "linked",
        context=TelemetryContext(correlation_id="linked-correlation"),
        links=(
            SpanLink(
                trace_id=source.trace_id,
                span_id=source.span_id,
                context=source.context,
                attributes={"secret": "do-not-export", "kind": "fan-in"},
            ),
        ),
    )
    record = telemetry.finish_span(linked, outcome=TelemetryOutcome.SUCCEEDED)
    assert record.links[0].attributes == {"secret": "[REDACTED]", "kind": "fan-in"}
