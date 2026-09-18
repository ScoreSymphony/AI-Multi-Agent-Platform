"""Canonical Control Plane observability and multi-agent trace projection."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureComponent,
    TimelineReader,
    timeline_entry_resource,
)
from ai_multi_agent_platform.observability.models import TimelineEntry
from ai_multi_agent_platform.observability.trace import (
    SpanReader,
    TraceFilter,
    TraceProjection,
    trace_node_resource,
    trace_usage_resource,
)

from .http import HTTPRequest, HTTPResponse
from .models import API_VERSION, PageQuery, RequestContext, paginate, validation_error
from .run_contract import ControlPlane as _RunControlPlane
from .run_contract import ControlPlaneHTTP as _RunControlPlaneHTTP
from .run_contract import build_openapi as _build_run_openapi
from .service import _event_resource


class _AsyncTimelineReader(Protocol):
    async def query_timeline_async(
        self,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[TimelineEntry, ...]: ...


class ControlPlane(_RunControlPlane):
    """Current Control Plane plus backend-neutral observability read projections."""

    _observability_timeline: TimelineReader | None = None
    _trace_projection: TraceProjection | None = None

    def bind_observability_timeline(self, timeline: TimelineReader | None) -> None:
        """Bind derived telemetry and assemble the default trace-explorer read projection."""

        self._observability_timeline = timeline
        span_reader = (
            cast(SpanReader, timeline)
            if timeline is not None and hasattr(timeline, "query_spans")
            else None
        )
        accounting = getattr(self, "accounting_service", None)
        usage_reader = None
        if accounting is not None:
            # Runtime import avoids coupling the early Control Plane composition graph to
            # accounting.__init__, which itself exposes Control Plane resource adapters.
            from ai_multi_agent_platform.accounting.trace import AccountingTraceUsageReader

            usage_reader = AccountingTraceUsageReader(accounting)
        self._trace_projection = TraceProjection(
            spans=span_reader,
            timeline=timeline,
            usage=usage_reader,
        )

    def bind_trace_projection(self, trace: TraceProjection | None) -> None:
        """Replace the read-only trace-explorer projection without changing telemetry ownership."""

        self._trace_projection = trace

    async def timeline(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:list", task_id, task)
        resources = [_event_resource(event) for event in await self._events.read_events(task_id)]
        telemetry = self._observability_timeline
        if telemetry is not None:
            if hasattr(telemetry, "query_timeline_async"):
                entries = await cast(_AsyncTimelineReader, telemetry).query_timeline_async(
                    task_id=task_id
                )
            else:
                entries = telemetry.query_timeline(task_id=task_id)
            resources.extend(
                timeline_entry_resource(entry)
                for entry in entries
                if entry.component is not FailureComponent.DOMAIN_KERNEL
            )
        return paginate(resources, query)

    async def task_trace(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:list", task_id, task)
        projection = self._trace_projection
        if projection is None:
            return {
                "items": [],
                "next_cursor": None,
                "total": 0,
                "limit": query.limit,
                "telemetry_state": "missing",
                "missing_sources": ["spans", "timeline", "usage"],
                "root_ids": [],
                "task_usage": [],
                "has_hierarchy": False,
            }
        snapshot = await projection.query(task_id=task_id, trace_filter=_trace_filter(query))
        resources = [trace_node_resource(node) for node in snapshot.nodes]
        page_query = PageQuery(
            limit=query.limit,
            cursor=query.cursor,
            sort=query.sort,
            direction=query.direction,
            fields=query.fields,
        )
        page = paginate(resources, page_query)
        parent_ids = {node.parent_id for node in snapshot.nodes if node.parent_id is not None}
        node_ids = {node.id for node in snapshot.nodes}
        roots = [
            node.id
            for node in snapshot.nodes
            if node.parent_id is None or node.parent_id not in node_ids
        ]
        result: dict[str, JsonValue] = dict(page)
        result["telemetry_state"] = snapshot.telemetry_state
        result["missing_sources"] = cast(JsonValue, list(snapshot.missing_sources))
        result["root_ids"] = cast(JsonValue, roots)
        result["task_usage"] = cast(
            JsonValue,
            [trace_usage_resource(record) for record in snapshot.task_usage],
        )
        result["has_hierarchy"] = bool(parent_ids)
        return result

    async def trace_node(
        self,
        context: RequestContext,
        task_id: str,
        node_id: str,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:list", task_id, task)
        projection = self._trace_projection
        if projection is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"trace node not found: {node_id}")
        node = await projection.get_node(task_id=task_id, node_id=node_id)
        if node is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"trace node not found: {node_id}")
        return trace_node_resource(node)


class ControlPlaneHTTP(_RunControlPlaneHTTP):
    """HTTP mapping for canonical timeline and trace explorer reads."""

    async def _tasks(
        self,
        request: HTTPRequest,
        context: RequestContext,
        query: PageQuery,
        segments: list[str],
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        control_plane = cast(ControlPlane, self._control_plane)
        if len(segments) == 3 and segments[2] == "trace" and request.method == "GET":
            page = await control_plane.task_trace(context, segments[1], query)
            return self._response(200, page, request_id, correlation_id)
        if len(segments) == 4 and segments[2] == "trace" and request.method == "GET":
            item = await control_plane.trace_node(context, segments[1], segments[3])
            return self._response(200, item, request_id, correlation_id)
        return await super()._tasks(request, context, query, segments, request_id, correlation_id)


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    specification = _build_run_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
    components = specification.setdefault("components", {}).setdefault("schemas", {})
    components["TraceNode"] = {
        "type": "object",
        "required": [
            "id",
            "type",
            "kind",
            "name",
            "timestamp",
            "outcome",
            "context",
            "attributes",
            "parallel_with",
            "usage",
            "resources",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "trace-node"},
            "kind": {"type": "string", "enum": ["span", "event"]},
            "name": {"type": "string"},
            "trace_id": {"type": ["string", "null"]},
            "span_id": {"type": ["string", "null"]},
            "parent_id": {"type": ["string", "null"]},
            "parent_span_id": {"type": ["string", "null"]},
            "timestamp": {"type": "string", "format": "date-time"},
            "finished_at": {"type": ["string", "null"], "format": "date-time"},
            "duration_seconds": {"type": ["number", "null"]},
            "outcome": {"type": "string"},
            "failure": {"type": ["object", "null"], "additionalProperties": True},
            "context": {"type": "object", "additionalProperties": True},
            "attributes": {"type": "object", "additionalProperties": True},
            "async_links": {"type": "array", "items": {"type": "object"}},
            "parallel_with": {"type": "array", "items": {"type": "string"}},
            "usage": {"type": "array", "items": {"type": "object"}},
            "resources": {"type": "array", "items": {"type": "object"}},
        },
        "additionalProperties": False,
    }
    trace_path = f"/api/{API_VERSION}/tasks/{{task_id}}/trace"
    node_path = f"{trace_path}/{{node_id}}"
    specification.setdefault("paths", {})[trace_path] = {
        "get": {
            "operationId": "getTaskTrace",
            "summary": "Inspect the canonical multi-agent Task trace",
            "parameters": [
                _path_parameter("task_id"),
                *_trace_query_parameters(),
            ],
            "responses": {"200": {"description": "Paginated trace nodes"}},
        }
    }
    specification["paths"][node_path] = {
        "get": {
            "operationId": "getTaskTraceNode",
            "summary": "Inspect one canonical Task trace node",
            "parameters": [
                _path_parameter("task_id"),
                _path_parameter("node_id"),
            ],
            "responses": {
                "200": {
                    "description": "Trace node",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/TraceNode"}}
                    },
                }
            },
        }
    }
    return specification


def _path_parameter(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }


def _trace_query_parameters() -> list[dict[str, Any]]:
    parameters: list[dict[str, Any]] = [
        {
            "name": "limit",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        {"name": "cursor", "in": "query", "schema": {"type": "string"}},
        {"name": "sort", "in": "query", "schema": {"type": "string", "default": "id"}},
        {
            "name": "direction",
            "in": "query",
            "schema": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
        },
        {"name": "fields", "in": "query", "schema": {"type": "string"}},
    ]
    for name in (
        "agent_id",
        "step_id",
        "model_config_id",
        "model_provider_id",
        "capability_id",
        "failure_component",
    ):
        parameters.append({"name": f"filter[{name}]", "in": "query", "schema": {"type": "string"}})
    for name in ("started_after", "started_before"):
        parameters.append(
            {
                "name": f"filter[{name}]",
                "in": "query",
                "schema": {"type": "string", "format": "date-time"},
            }
        )
    return parameters


def _trace_filter(query: PageQuery) -> TraceFilter:
    filters = query.filters or {}
    return TraceFilter(
        agent_id=filters.get("agent_id"),
        step_id=filters.get("step_id"),
        model_config_id=filters.get("model_config_id"),
        model_provider_id=filters.get("model_provider_id"),
        capability_id=filters.get("capability_id"),
        failure_component=filters.get("failure_component"),
        started_after=_optional_datetime(filters.get("started_after")),
        started_before=_optional_datetime(filters.get("started_before")),
    )


def _optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise validation_error("trace-time-filter", "trace time filters must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise validation_error("trace-time-filter", "trace time filters must be timezone-aware")
    return parsed
