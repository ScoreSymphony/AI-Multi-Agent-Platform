from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
)
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    PageQuery,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.observability import InMemoryExporter
from ai_multi_agent_platform.observability.models import (
    SpanRecord,
    TelemetryContext,
    TelemetryOutcome,
)
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class _TraceAuthorization(FakeAuthorizationProvider):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.resource_ref == "artifact_901":
            return AuthorizationDecision(allowed=False, reason="artifact-hidden")
        return AuthorizationDecision(allowed=True, reason="task-trace-visible")


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-901",
        correlation_id="request-901",
        actor=ActorContext(
            principal_ref="user:trace",
            owner_type="user",
            owner_id="trace",
        ),
    )


def _stack() -> tuple[ControlPlane, PlatformKernel, InMemoryExporter]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control = ControlPlane(kernel=kernel, events=repository)
    exporter = InMemoryExporter()
    control.bind_observability_timeline(exporter)
    return control, kernel, exporter


def _emit_trace(exporter: InMemoryExporter, task_id: str, run_id: str) -> None:
    started = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)
    root = SpanRecord(
        name="run.lifecycle",
        trace_id="trace_901_cp",
        span_id="span_root",
        context=TelemetryContext(task_id=task_id, run_id=run_id, correlation_id=task_id),
        started_at=started,
        finished_at=started + timedelta(seconds=10),
        duration_seconds=10,
        outcome=TelemetryOutcome.SUCCEEDED,
    )
    child = SpanRecord(
        name="agent.run",
        trace_id=root.trace_id,
        span_id="span_agent",
        parent_span_id=root.span_id,
        context=TelemetryContext(
            task_id=task_id,
            run_id=run_id,
            step_id="step_901",
            agent_id="agent_901",
            correlation_id=task_id,
        ),
        started_at=started + timedelta(seconds=1),
        finished_at=started + timedelta(seconds=8),
        duration_seconds=7,
        outcome=TelemetryOutcome.SUCCEEDED,
        attributes={
            "artifact_id": "artifact_901",
            "artifact_content": "private artifact body",
        },
    )
    exporter.emit_span(root)
    exporter.emit_span(child)


def test_control_plane_trace_is_authorized_filtered_paginated_and_content_safe() -> None:
    async def scenario() -> None:
        control, kernel, exporter = _stack()
        task = await kernel.create_task(
            idempotency_key="issue-901-control-plane",
            title="Trace me",
            objective="Exercise the canonical trace projection",
            owner_type="user",
            owner_id="trace",
        )
        _emit_trace(exporter, task.task_id, "run_901")

        first = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(limit=1, sort="timestamp", direction="asc"),
        )
        assert first["total"] == 2
        assert first["next_cursor"] is not None
        assert first["telemetry_state"] == "available"
        assert first["missing_sources"] == ["usage"]

        filtered = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(filters={"agent_id": "agent_901"}, sort="timestamp"),
        )
        items = filtered["items"]
        assert isinstance(items, list)
        assert len(items) == 1
        item = items[0]
        assert isinstance(item, dict)
        assert item["id"] == "span_agent"
        assert item["parent_id"] == "span_root"
        assert "artifact_content" not in item["attributes"]
        serialized = repr(item)
        assert "private artifact body" not in serialized
        assert any(
            isinstance(link, dict)
            and link.get("type") == "artifact"
            and link.get("id") == "artifact_901"
            for link in item["resources"]
        )
        assert all(
            set(link) == {"type", "id", "href"}
            for link in item["resources"]
            if isinstance(link, dict)
        )

        detail = await control.trace_node(_context(), task.task_id, "span_agent")
        assert detail["id"] == "span_agent"
        assert "private artifact body" not in repr(detail)

    asyncio.run(scenario())


def test_http_and_openapi_publish_the_same_trace_projection() -> None:
    async def scenario() -> None:
        control, kernel, exporter = _stack()
        task = await kernel.create_task(
            idempotency_key="issue-901-http",
            title="HTTP trace",
            objective="Expose trace through versioned Control Plane HTTP",
            owner_type="user",
            owner_id="trace",
        )
        _emit_trace(exporter, task.task_id, "run_http_901")
        http = ControlPlaneHTTP(control)
        response = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task.task_id}/trace",
                trusted_actor=_context().actor,
                query={"filter[agent_id]": "agent_901"},
            )
        )
        assert response.status == 200
        assert isinstance(response.body, dict)
        assert response.body["total"] == 1
        assert response.body["items"][0]["id"] == "span_agent"

        detail = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task.task_id}/trace/span_agent",
                trusted_actor=_context().actor,
            )
        )
        assert detail.status == 200
        assert isinstance(detail.body, dict)
        assert detail.body["id"] == "span_agent"

    asyncio.run(scenario())

    paths = build_openapi()["paths"]
    assert "/api/v1/tasks/{task_id}/trace" in paths
    assert "/api/v1/tasks/{task_id}/trace/{node_id}" in paths


def test_large_trace_is_cursor_paginated_in_bounded_pages() -> None:
    async def scenario() -> None:
        control, kernel, exporter = _stack()
        task = await kernel.create_task(
            idempotency_key="issue-901-large-trace",
            title="Large trace",
            objective="Prove large trace reads stay bounded",
            owner_type="user",
            owner_id="trace",
        )
        started = datetime(2026, 9, 15, 21, 0, tzinfo=UTC)
        for index in range(450):
            offset = timedelta(milliseconds=index)
            exporter.emit_span(
                SpanRecord(
                    name="agent.run",
                    trace_id="trace_901_large",
                    span_id=f"span_large_{index:03d}",
                    context=TelemetryContext(
                        task_id=task.task_id,
                        run_id="run_large_901",
                        step_id=f"step_large_{index:03d}",
                        correlation_id=task.task_id,
                    ),
                    started_at=started + offset,
                    finished_at=started + offset + timedelta(milliseconds=1),
                    duration_seconds=0.001,
                    outcome=TelemetryOutcome.SUCCEEDED,
                )
            )

        first = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(limit=200, sort="timestamp", direction="asc"),
        )
        assert first["total"] == 450
        assert len(first["items"]) == 200
        assert first["next_cursor"] is not None

        second = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(
                limit=200,
                cursor=first["next_cursor"],
                sort="timestamp",
                direction="asc",
            ),
        )
        assert len(second["items"]) == 200
        assert second["next_cursor"] is not None

        third = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(
                limit=200,
                cursor=second["next_cursor"],
                sort="timestamp",
                direction="asc",
            ),
        )
        assert len(third["items"]) == 50
        assert third["next_cursor"] is None

    asyncio.run(scenario())


def test_trace_does_not_resolve_unauthorized_underlying_resource() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        authorization = _TraceAuthorization()
        control = ControlPlane(
            kernel=kernel,
            events=repository,
            authorization=authorization,
        )
        exporter = InMemoryExporter()
        control.bind_observability_timeline(exporter)
        task = await kernel.create_task(
            idempotency_key="issue-901-resource-authorization",
            title="Authorized trace, unauthorized artifact",
            objective="Trace must never resolve the linked resource body",
            owner_type="user",
            owner_id="trace",
        )
        _emit_trace(exporter, task.task_id, "run_resource_auth_901")

        page = await control.task_trace(
            _context(),
            task.task_id,
            PageQuery(filters={"agent_id": "agent_901"}),
        )
        assert "private artifact body" not in repr(page)
        items = page["items"]
        assert isinstance(items, list) and len(items) == 1
        item = items[0]
        assert isinstance(item, dict)
        assert {
            "type": "artifact",
            "id": "artifact_901",
            "href": "/api/v1/artifacts/artifact_901",
        } in item["resources"]
        assert all(call.resource_ref != "artifact_901" for call in authorization.calls)

    asyncio.run(scenario())
