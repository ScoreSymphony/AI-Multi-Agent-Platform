"""Derived multi-agent trace projection over canonical observability data."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .integrations import TimelineReader, timeline_entry_resource
from .models import (
    CapturePolicy,
    FailureClassification,
    SpanRecord,
    TelemetryContext,
    TelemetryOutcome,
    TimelineEntry,
)

_CONTENT_KEYS = frozenset(
    {
        "arguments",
        "body",
        "content",
        "file_content",
        "input",
        "messages",
        "output",
        "payload",
        "prompt",
        "request_body",
        "response",
        "response_body",
        "tool_input",
        "tool_output",
    }
)
_SENSITIVE_KEYS = CapturePolicy().sensitive_keys
_REDACTED = "[REDACTED]"
_OMIT = object()


class SpanReader(Protocol):
    def query_spans(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[SpanRecord, ...]: ...


class _AsyncTimelineReader(Protocol):
    async def query_timeline_async(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]: ...


@dataclass(frozen=True, slots=True)
class TraceUsageRecord:
    id: str
    metric_type: str
    unit: str
    quality: str
    source: str
    timestamp: datetime
    scope: dict[str, str] = field(default_factory=dict)
    quantity: float | None = None
    provider: str | None = None
    cost_amount: float | None = None
    currency: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    precision: float | None = None
    confidence: float | None = None
    provenance: dict[str, JsonValue] = field(default_factory=dict)


class TraceUsageReader(Protocol):
    def query_trace_usage(self, *, task_id: str) -> tuple[TraceUsageRecord, ...]: ...


@dataclass(frozen=True, slots=True)
class TraceFilter:
    agent_id: str | None = None
    step_id: str | None = None
    model_config_id: str | None = None
    model_provider_id: str | None = None
    capability_id: str | None = None
    failure_component: str | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class TraceNode:
    id: str
    kind: str
    name: str
    context: TelemetryContext
    timestamp: datetime
    outcome: TelemetryOutcome
    trace_id: str | None = None
    span_id: str | None = None
    parent_id: str | None = None
    parent_span_id: str | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    failure: FailureClassification | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    async_links: tuple[dict[str, JsonValue], ...] = ()
    parallel_with: tuple[str, ...] = ()
    usage: tuple[TraceUsageRecord, ...] = ()
    resources: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TraceSnapshot:
    task_id: str
    nodes: tuple[TraceNode, ...]
    telemetry_state: str
    missing_sources: tuple[str, ...]
    task_usage: tuple[TraceUsageRecord, ...] = ()


class TraceProjection:
    """Read-only composition; canonical state remains with its owning domains."""

    def __init__(
        self,
        *,
        spans: SpanReader | None,
        timeline: TimelineReader | None,
        usage: TraceUsageReader | None = None,
    ) -> None:
        self._spans = spans
        self._timeline = timeline
        self._usage = usage

    async def query(
        self,
        *,
        task_id: str,
        trace_filter: TraceFilter | None = None,
    ) -> TraceSnapshot:
        spans = self._spans.query_spans(task_id=task_id) if self._spans is not None else ()
        timeline = await self._timeline_for(task_id)
        usage = self._usage.query_trace_usage(task_id=task_id) if self._usage is not None else ()
        assigned, task_usage = _assign_usage(spans, usage)
        nodes = _mark_parallel((*_span_nodes(spans, assigned), *_event_nodes(timeline, spans)))
        selected = trace_filter or TraceFilter()
        nodes = tuple(
            node
            for node in sorted(nodes, key=lambda item: (item.timestamp, item.kind, item.id))
            if _matches_filter(node, selected)
        )
        missing = tuple(
            name
            for name, reader in (
                ("spans", self._spans),
                ("timeline", self._timeline),
                ("usage", self._usage),
            )
            if reader is None
        )
        primary_missing = {name for name in missing if name in {"spans", "timeline"}}
        if len(primary_missing) == 2:
            state = "missing"
        elif primary_missing:
            state = "degraded"
        else:
            state = "available"
        return TraceSnapshot(task_id, nodes, state, missing, task_usage)

    async def get_node(self, *, task_id: str, node_id: str) -> TraceNode | None:
        snapshot = await self.query(task_id=task_id)
        return next((node for node in snapshot.nodes if node.id == node_id), None)

    async def _timeline_for(self, task_id: str) -> tuple[TimelineEntry, ...]:
        if self._timeline is None:
            return ()
        if hasattr(self._timeline, "query_timeline_async"):
            reader = cast(_AsyncTimelineReader, self._timeline)
            return await reader.query_timeline_async(task_id=task_id)
        return self._timeline.query_timeline(task_id=task_id)


def trace_node_resource(node: TraceNode) -> dict[str, JsonValue]:
    failure: JsonValue = None
    if node.failure is not None:
        failure = {
            "component": node.failure.component.value,
            "code": node.failure.code,
            "retryable": node.failure.retryable,
        }
    return {
        "id": node.id,
        "type": "trace-node",
        "kind": node.kind,
        "name": node.name,
        "trace_id": node.trace_id,
        "span_id": node.span_id,
        "parent_id": node.parent_id,
        "parent_span_id": node.parent_span_id,
        "timestamp": node.timestamp.isoformat(),
        "finished_at": node.finished_at.isoformat() if node.finished_at else None,
        "duration_seconds": node.duration_seconds,
        "outcome": node.outcome.value,
        "failure": failure,
        "context": dict(node.context.fields()),
        "attributes": dict(node.attributes),
        "async_links": [dict(link) for link in node.async_links],
        "parallel_with": list(node.parallel_with),
        "usage": [trace_usage_resource(record) for record in node.usage],
        "resources": [dict(link) for link in node.resources],
    }


def trace_usage_resource(record: TraceUsageRecord) -> dict[str, JsonValue]:
    return {
        "id": record.id,
        "metric_type": record.metric_type,
        "unit": record.unit,
        "quality": record.quality,
        "source": record.source,
        "timestamp": record.timestamp.isoformat(),
        "scope": dict(record.scope),
        "quantity": record.quantity,
        "provider": record.provider,
        "cost_amount": record.cost_amount,
        "currency": record.currency,
        "correlation_id": record.correlation_id,
        "causation_id": record.causation_id,
        "precision": record.precision,
        "confidence": record.confidence,
        "provenance": _safe_attributes(record.provenance),
    }


def _span_nodes(
    spans: tuple[SpanRecord, ...],
    usage: dict[str, tuple[TraceUsageRecord, ...]],
) -> tuple[TraceNode, ...]:
    span_ids = {span.span_id for span in spans}
    return tuple(
        TraceNode(
            id=span.span_id,
            kind="span",
            name=span.name,
            context=span.context,
            timestamp=span.started_at,
            finished_at=span.finished_at,
            duration_seconds=span.duration_seconds,
            outcome=span.outcome,
            trace_id=span.trace_id,
            span_id=span.span_id,
            parent_id=span.parent_span_id if span.parent_span_id in span_ids else None,
            parent_span_id=span.parent_span_id,
            failure=span.failure,
            attributes=_safe_attributes(span.attributes),
            async_links=tuple(
                {
                    "trace_id": link.trace_id,
                    "span_id": link.span_id,
                    "context": dict(link.context.fields()),
                    "attributes": _safe_attributes(link.attributes),
                }
                for link in span.links
            ),
            usage=usage.get(span.span_id, ()),
            resources=_resource_links(span.context, span.attributes),
        )
        for span in spans
    )


def _event_nodes(
    entries: tuple[TimelineEntry, ...],
    spans: tuple[SpanRecord, ...],
) -> tuple[TraceNode, ...]:
    result: list[TraceNode] = []
    for entry in entries:
        parent = _event_parent(entry, spans)
        result.append(
            TraceNode(
                id=str(timeline_entry_resource(entry)["id"]),
                kind="event",
                name=entry.event_name,
                context=entry.context,
                timestamp=entry.timestamp,
                finished_at=entry.timestamp,
                duration_seconds=entry.duration_seconds,
                outcome=entry.outcome,
                trace_id=parent.trace_id if parent else None,
                parent_id=parent.span_id if parent else None,
                parent_span_id=parent.span_id if parent else None,
                failure=entry.failure,
                attributes=_safe_attributes(entry.attributes),
                resources=_resource_links(entry.context, entry.attributes),
            )
        )
    return tuple(result)


def _event_parent(entry: TimelineEntry, spans: tuple[SpanRecord, ...]) -> SpanRecord | None:
    ranked: list[tuple[int, float, SpanRecord]] = []
    for span in spans:
        outside_span = not (span.started_at <= entry.timestamp <= span.finished_at)
        if entry.context.task_id != span.context.task_id or outside_span:
            continue
        score = _context_match_score(entry.context, span.context)
        if score > 0:
            ranked.append((score, -span.duration_seconds, span))
    ranked.sort(key=lambda item: (item[0], item[1], item[2].span_id), reverse=True)
    return ranked[0][2] if ranked else None


def _context_match_score(left: TelemetryContext, right: TelemetryContext) -> int:
    fields = (
        "run_id",
        "step_id",
        "agent_id",
        "team_id",
        "model_call_id",
        "model_config_id",
        "model_provider_id",
        "tool_invocation_id",
        "capability_id",
        "worker_job_id",
        "worker_id",
        "node_id",
        "approval_id",
        "verification_id",
    )
    return sum(
        1
        for name in fields
        if (value := getattr(left, name)) is not None and value == getattr(right, name)
    )


def _mark_parallel(nodes: tuple[TraceNode, ...]) -> tuple[TraceNode, ...]:
    spans = [node for node in nodes if node.kind == "span" and node.finished_at is not None]
    parallel: dict[str, set[str]] = {node.id: set() for node in spans}
    groups: dict[str | None, list[TraceNode]] = {}
    for node in spans:
        groups.setdefault(node.parent_id, []).append(node)
    for siblings in groups.values():
        for index, left in enumerate(siblings):
            for right in siblings[index + 1 :]:
                right_end = cast(datetime, right.finished_at)
                left_end = cast(datetime, left.finished_at)
                if left.timestamp < right_end and right.timestamp < left_end:
                    parallel[left.id].add(right.id)
                    parallel[right.id].add(left.id)
    return tuple(
        replace(node, parallel_with=tuple(sorted(parallel.get(node.id, set())))) for node in nodes
    )


def _assign_usage(
    spans: tuple[SpanRecord, ...],
    records: tuple[TraceUsageRecord, ...],
) -> tuple[dict[str, tuple[TraceUsageRecord, ...]], tuple[TraceUsageRecord, ...]]:
    assigned: dict[str, list[TraceUsageRecord]] = {}
    task_usage: list[TraceUsageRecord] = []
    for record in records:
        ranked = [
            (score, span)
            for span in spans
            if (score := _usage_score(record.scope, span.context)) > 0
        ]
        if not ranked:
            task_usage.append(record)
            continue

        best_score = max(score for score, _ in ranked)
        candidates = [span for score, span in ranked if score == best_score]
        candidates = _disambiguate_usage(record, candidates)
        if len(candidates) != 1:
            # Canonical accounting remains authoritative. If attribution does not identify one
            # span unambiguously, keep the record at Task scope rather than inventing a call.
            task_usage.append(record)
            continue
        assigned.setdefault(candidates[0].span_id, []).append(record)
    return ({key: tuple(value) for key, value in assigned.items()}, tuple(task_usage))


def _disambiguate_usage(
    record: TraceUsageRecord,
    candidates: list[SpanRecord],
) -> list[SpanRecord]:
    if len(candidates) <= 1:
        return candidates

    if record.correlation_id is not None:
        correlated = [
            span for span in candidates if span.context.correlation_id == record.correlation_id
        ]
        if len(correlated) == 1:
            return correlated
        if correlated:
            candidates = correlated

    if record.causation_id is not None:
        caused = [span for span in candidates if span.context.causation_id == record.causation_id]
        if len(caused) == 1:
            return caused
        if caused:
            candidates = caused

    temporal = [
        span for span in candidates if span.started_at <= record.timestamp <= span.finished_at
    ]
    if len(temporal) == 1:
        return temporal
    return candidates


def _usage_score(scope: dict[str, str], context: TelemetryContext) -> int:
    fields = context.fields()
    score = 0
    for name in (
        "run_id",
        "agent_id",
        "team_id",
        "capability_id",
        "model_config_id",
        "model_provider_id",
        "worker_id",
        "node_id",
    ):
        value = scope.get(name)
        if value is None:
            continue
        if fields.get(name) != value:
            return 0
        score += 1
    return score


def _matches_filter(node: TraceNode, selected: TraceFilter) -> bool:
    for name in (
        "agent_id",
        "step_id",
        "model_config_id",
        "model_provider_id",
        "capability_id",
    ):
        wanted = getattr(selected, name)
        if wanted is not None and getattr(node.context, name) != wanted:
            return False
    if selected.failure_component is not None and (
        node.failure is None or node.failure.component.value != selected.failure_component
    ):
        return False
    if selected.started_after is not None and node.timestamp < selected.started_after:
        return False
    if selected.started_before is not None and node.timestamp > selected.started_before:
        return False
    return True


def _resource_links(
    context: TelemetryContext,
    attributes: dict[str, JsonValue],
) -> tuple[dict[str, str], ...]:
    links: list[dict[str, str]] = []
    for resource_type, resource_id, collection in (
        ("task", context.task_id, "tasks"),
        ("run", context.run_id, "runs"),
        ("step", context.step_id, "steps"),
        ("agent", context.agent_id, "agents"),
        ("agent-team", context.team_id, "agent-teams"),
        ("capability", context.capability_id, "capabilities"),
        ("approval", context.approval_id, "approvals"),
        ("verification", context.verification_id, "verifications"),
    ):
        if resource_id:
            links.append(
                {
                    "type": resource_type,
                    "id": resource_id,
                    "href": f"/api/v1/{collection}/{resource_id}",
                }
            )
    for key, resource_type, collection in (
        ("plan_id", "plan", "plans"),
        ("agent_run_id", "agent-run", "agent-runs"),
        ("artifact_id", "artifact", "artifacts"),
        ("result_id", "result", "results"),
        ("verification_result_id", "verification-result", "verification-results"),
        ("handoff_id", "agent-handoff", "agent-handoffs"),
        ("context_bundle_id", "context-bundle", "context-bundles"),
    ):
        values = _strings(attributes.get(key)) + _strings(attributes.get(f"{key}s"))
        links.extend(
            {
                "type": resource_type,
                "id": value,
                "href": f"/api/v1/{collection}/{value}",
            }
            for value in values
        )
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for link in links:
        unique[(link["type"], link["id"])] = link
    return tuple(unique.values())


def _safe_attributes(values: dict[str, JsonValue]) -> dict[str, JsonValue]:
    safe: dict[str, JsonValue] = {}
    for key, value in values.items():
        projected = _safe_value(key, value)
        if projected is not _OMIT:
            safe[key] = cast(JsonValue, projected)
    return safe


def _safe_value(key: str, value: JsonValue) -> JsonValue | object:
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _CONTENT_KEYS or any(
        normalized.endswith(f"_{suffix}") for suffix in _CONTENT_KEYS
    ):
        return _OMIT
    if _sensitive_key(normalized):
        return _REDACTED
    if isinstance(value, dict):
        return _safe_attributes(value)
    if isinstance(value, list):
        return [_safe_nested(item) for item in value]
    return value


def _safe_nested(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return _safe_attributes(value)
    if isinstance(value, list):
        return [_safe_nested(item) for item in value]
    return value


def _sensitive_key(normalized: str) -> bool:
    if normalized in _SENSITIVE_KEYS:
        return True
    if normalized.endswith(("_secret", "_password", "_credential")):
        return True
    return normalized.endswith("_token") and normalized != "token_count"


def _strings(value: JsonValue | None) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value,)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, str) and item.strip())
    return ()
