from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.observability.models import (
    FailureClassification,
    FailureComponent,
    SpanRecord,
    TelemetryContext,
    TelemetryOutcome,
    TimelineEntry,
)
from ai_multi_agent_platform.observability.trace import (
    TraceProjection,
    TraceUsageRecord,
    trace_node_resource,
    trace_usage_resource,
)


class _SpanReader:
    def __init__(self, spans: tuple[SpanRecord, ...]) -> None:
        self._spans = spans

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
            for span in self._spans
            if (task_id is None or span.context.task_id == task_id)
            and (run_id is None or span.context.run_id == run_id)
            and (trace_id is None or span.trace_id == trace_id)
            and (correlation_id is None or span.context.correlation_id == correlation_id)
        )


class _TimelineReader:
    def __init__(self, entries: tuple[TimelineEntry, ...]) -> None:
        self._entries = entries

    def query_timeline(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]:
        return tuple(
            entry
            for entry in self._entries
            if (task_id is None or entry.context.task_id == task_id)
            and (run_id is None or entry.context.run_id == run_id)
            and (correlation_id is None or entry.context.correlation_id == correlation_id)
        )


class _UsageReader:
    def __init__(self, records: tuple[TraceUsageRecord, ...]) -> None:
        self._records = records

    def query_trace_usage(self, *, task_id: str) -> tuple[TraceUsageRecord, ...]:
        return tuple(record for record in self._records if record.scope.get("task_id") == task_id)


def _span(
    span_id: str,
    *,
    started_at: datetime,
    finished_at: datetime,
    context: TelemetryContext,
    name: str = "model.generate",
    parent_span_id: str | None = None,
    failure: FailureClassification | None = None,
    attributes: dict[str, object] | None = None,
) -> SpanRecord:
    return SpanRecord(
        name=name,
        trace_id="trace_901_hardening",
        span_id=span_id,
        parent_span_id=parent_span_id,
        context=context,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=(finished_at - started_at).total_seconds(),
        outcome=TelemetryOutcome.FAILED if failure else TelemetryOutcome.SUCCEEDED,
        failure=failure,
        attributes=attributes or {},  # type: ignore[arg-type]
    )


def test_ambiguous_usage_remains_at_task_scope() -> None:
    started = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    task_id = "task_usage_ambiguous"
    context = TelemetryContext(
        task_id=task_id,
        run_id="run_shared",
        model_config_id="model_shared",
        model_provider_id="provider_shared",
    )
    spans = (
        _span(
            "span_call_a",
            started_at=started,
            finished_at=started + timedelta(seconds=2),
            context=context,
        ),
        _span(
            "span_call_b",
            started_at=started + timedelta(seconds=3),
            finished_at=started + timedelta(seconds=5),
            context=context,
        ),
    )
    usage = TraceUsageRecord(
        id="usage_ambiguous",
        metric_type="model.tokens.total",
        unit="tokens",
        quality="reported",
        source="provider",
        timestamp=started + timedelta(seconds=10),
        scope={
            "task_id": task_id,
            "run_id": "run_shared",
            "model_config_id": "model_shared",
            "model_provider_id": "provider_shared",
        },
        quantity=42,
    )
    projection = TraceProjection(
        spans=_SpanReader(spans),
        timeline=None,
        usage=_UsageReader((usage,)),
    )

    snapshot = asyncio.run(projection.query(task_id=task_id))

    assert snapshot.task_usage == (usage,)
    assert all(not node.usage for node in snapshot.nodes)


def test_projection_redacts_capture_policy_keys_and_content_from_raw_readers() -> None:
    started = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
    task_id = "task_trace_redaction"
    span = _span(
        "span_sensitive",
        started_at=started,
        finished_at=started + timedelta(seconds=1),
        context=TelemetryContext(task_id=task_id),
        attributes={
            "api_key": "api-secret",
            "apikey": "api-secret-2",
            "session": "session-secret",
            "session_id": "session-secret-2",
            "db_password": "database-secret",
            "client_credential": "credential-secret",
            "prompt": "private prompt body",
            "tool_input": "private tool payload",
            "token_count": 17,
        },
    )
    usage = TraceUsageRecord(
        id="usage_sensitive",
        metric_type="model.tokens.total",
        unit="tokens",
        quality="reported",
        source="provider",
        timestamp=started,
        scope={"task_id": task_id},
        quantity=17,
        provenance={
            "api_key": "usage-api-secret",
            "session_id": "usage-session-secret",
            "token_count": 17,
        },
    )
    projection = TraceProjection(
        spans=_SpanReader((span,)),
        timeline=None,
        usage=_UsageReader((usage,)),
    )

    snapshot = asyncio.run(projection.query(task_id=task_id))
    serialized_node = repr(trace_node_resource(snapshot.nodes[0]))
    serialized_usage = repr(trace_usage_resource(snapshot.task_usage[0]))

    for secret in (
        "api-secret",
        "api-secret-2",
        "session-secret",
        "session-secret-2",
        "database-secret",
        "credential-secret",
        "private prompt body",
        "private tool payload",
        "usage-api-secret",
        "usage-session-secret",
    ):
        assert secret not in serialized_node
        assert secret not in serialized_usage
    assert "[REDACTED]" in serialized_node
    assert "[REDACTED]" in serialized_usage
    assert "17" in serialized_node
    assert "17" in serialized_usage


def test_projection_surfaces_tool_approval_handoff_replan_retry_and_verification() -> None:
    started = datetime(2026, 9, 15, 22, 0, tzinfo=UTC)
    task_id = "task_multi_agent_visibility"
    run_id = "run_multi_agent_visibility"
    root_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        correlation_id=task_id,
    )
    agent_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_execute",
        agent_id="agent_coder",
        correlation_id=task_id,
    )
    model_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_execute",
        agent_id="agent_coder",
        model_config_id="model_local",
        model_provider_id="provider_local",
        correlation_id=task_id,
    )
    tool_context = TelemetryContext(
        task_id=task_id,
        run_id=run_id,
        step_id="step_execute",
        agent_id="agent_coder",
        capability_id="capability_shell",
        tool_invocation_id="tool_invocation_901",
        correlation_id=task_id,
    )
    retry_failure = FailureClassification(
        component=FailureComponent.EXECUTION,
        code="worker_transient_failure",
        retryable=True,
    )
    spans = (
        _span(
            "span_root",
            name="run.lifecycle",
            started_at=started,
            finished_at=started + timedelta(seconds=12),
            context=root_context,
        ),
        _span(
            "span_agent",
            name="agent.run",
            parent_span_id="span_root",
            started_at=started + timedelta(seconds=1),
            finished_at=started + timedelta(seconds=10),
            context=agent_context,
        ),
        _span(
            "span_model",
            name="model.generate",
            parent_span_id="span_agent",
            started_at=started + timedelta(seconds=2),
            finished_at=started + timedelta(seconds=4),
            context=model_context,
        ),
        _span(
            "span_tool",
            name="tool.invoke",
            parent_span_id="span_agent",
            started_at=started + timedelta(seconds=4),
            finished_at=started + timedelta(seconds=6),
            context=tool_context,
            attributes={"result_id": "result_901"},
        ),
    )
    timeline = (
        TimelineEntry(
            event_name="approval.decided",
            component=FailureComponent.AUTHORIZATION_APPROVAL,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                step_id="step_execute",
                approval_id="approval_901",
                correlation_id=task_id,
            ),
            timestamp=started + timedelta(seconds=6),
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"decision": "approved", "policy_ref": "policy_safe"},
        ),
        TimelineEntry(
            event_name="execution.retry.scheduled",
            component=FailureComponent.EXECUTION,
            context=agent_context,
            timestamp=started + timedelta(seconds=7),
            outcome=TelemetryOutcome.FAILED,
            failure=retry_failure,
            attributes={"retry_reason": "transient worker failure", "attempt": 2},
        ),
        TimelineEntry(
            event_name="handoff.consumed",
            component=FailureComponent.ORCHESTRATION,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                step_id="step_execute",
                agent_id="agent_coder",
                correlation_id=task_id,
            ),
            timestamp=started + timedelta(seconds=8),
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={
                "handoff_id": "handoff_901",
                "context_bundle_id": "context_bundle_901",
                "plan_id": "plan_901",
                "plan_revision": 2,
            },
        ),
        TimelineEntry(
            event_name="planning.replanned",
            component=FailureComponent.ORCHESTRATION,
            context=root_context,
            timestamp=started + timedelta(seconds=9),
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={
                "plan_id": "plan_902",
                "plan_revision": 3,
                "replan_reason": "verification repair",
            },
        ),
        TimelineEntry(
            event_name="verification.completed",
            component=FailureComponent.VERIFICATION,
            context=TelemetryContext(
                task_id=task_id,
                run_id=run_id,
                verification_id="verification_901",
                correlation_id=task_id,
            ),
            timestamp=started + timedelta(seconds=11),
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes={"verification_result_id": "verification_result_901"},
        ),
    )
    projection = TraceProjection(
        spans=_SpanReader(spans),
        timeline=_TimelineReader(timeline),
    )

    snapshot = asyncio.run(projection.query(task_id=task_id))
    by_id = {node.id: node for node in snapshot.nodes}
    by_name = {node.name: node for node in snapshot.nodes}

    assert by_id["span_model"].parent_id == "span_agent"
    assert by_id["span_tool"].parent_id == "span_agent"
    assert any(link["id"] == "result_901" for link in by_id["span_tool"].resources)
    assert by_name["approval.decided"].context.approval_id == "approval_901"
    retry = by_name["execution.retry.scheduled"]
    assert retry.failure is not None
    assert retry.failure.code == "worker_transient_failure"
    assert retry.failure.retryable is True
    assert retry.attributes["retry_reason"] == "transient worker failure"

    handoff = by_name["handoff.consumed"]
    handoff_links = {(link["type"], link["id"]) for link in handoff.resources}
    assert ("agent-handoff", "handoff_901") in handoff_links
    assert ("context-bundle", "context_bundle_901") in handoff_links
    assert ("plan", "plan_901") in handoff_links
    assert handoff.attributes["plan_revision"] == 2

    replan = by_name["planning.replanned"]
    assert replan.attributes["plan_revision"] == 3
    assert replan.attributes["replan_reason"] == "verification repair"
    assert any(link["id"] == "plan_902" for link in replan.resources)

    verification = by_name["verification.completed"]
    assert verification.context.verification_id == "verification_901"
    assert any(
        link["type"] == "verification-result" and link["id"] == "verification_result_901"
        for link in verification.resources
    )


def test_exporter_replacement_preserves_canonical_projection_semantics() -> None:
    started = datetime(2026, 9, 15, 23, 0, tzinfo=UTC)
    task_id = "task_exporter_replace"
    span = _span(
        "span_replace",
        name="agent.run",
        started_at=started,
        finished_at=started + timedelta(seconds=2),
        context=TelemetryContext(
            task_id=task_id,
            run_id="run_replace",
            agent_id="agent_replace",
            correlation_id=task_id,
        ),
        attributes={"artifact_id": "artifact_replace"},
    )
    event = TimelineEntry(
        event_name="verification.completed",
        component=FailureComponent.VERIFICATION,
        context=TelemetryContext(
            task_id=task_id,
            run_id="run_replace",
            verification_id="verification_replace",
            correlation_id=task_id,
        ),
        timestamp=started + timedelta(seconds=1),
        outcome=TelemetryOutcome.SUCCEEDED,
    )
    exporter = InMemoryExporter()
    exporter.emit_span(span)
    exporter.emit_timeline(event)

    in_memory = TraceProjection(spans=exporter, timeline=exporter)
    replacement = TraceProjection(
        spans=_SpanReader((span,)),
        timeline=_TimelineReader((event,)),
    )

    first = asyncio.run(in_memory.query(task_id=task_id))
    second = asyncio.run(replacement.query(task_id=task_id))

    assert first.telemetry_state == second.telemetry_state == "available"
    assert first.missing_sources == second.missing_sources == ("usage",)
    assert [trace_node_resource(node) for node in first.nodes] == [
        trace_node_resource(node) for node in second.nodes
    ]
