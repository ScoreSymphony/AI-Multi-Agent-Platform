from __future__ import annotations

import asyncio
import json
from typing import Any

from control_plane_contract_helpers import api_headers, assert_error_envelope, assert_page

from ai_multi_agent_platform.contracts import ErrorCode
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    ControlPlane,
    ControlPlaneASGI,
    ControlPlaneHTTP,
    HTTPRequest,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeEventProvider,
    FakeFailure,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _context(key: str | None = None) -> RequestContext:
    return RequestContext(
        request_id="request-test",
        correlation_id="correlation-test",
        actor=ActorContext(
            principal_ref="user:test",
            owner_type="user",
            owner_id="test",
        ),
        idempotency_key=key,
    )


def _stack(
    *,
    live: bool = False,
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[
    ControlPlane,
    PlatformKernel,
    InMemoryKernelRepository,
    FakeEventProvider | None,
]:
    repository = InMemoryKernelRepository()
    live_events = FakeEventProvider() if live else None
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
        event_sink=live_events,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        live_events=live_events,
    )
    return control_plane, kernel, repository, live_events


async def _create_task(
    http: ControlPlaneHTTP,
    *,
    key: str,
    title: str = "Task",
    project_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "objective": f"Objective for {title}",
        "owner_type": "user",
        "owner_id": "test",
    }
    if project_id is not None:
        body["project_id"] = project_id
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=api_headers(idempotency_key=key),
            body=body,
        )
    )
    assert response.status == 201
    assert isinstance(response.body, dict)
    return response.body


def test_project_and_workspace_identity_baseline() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        project_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="project-1"),
                body={"name": "Project", "owner_type": "user", "owner_id": "test"},
            )
        )
        assert project_response.status == 201
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str) and project_id.startswith("project_")

        workspace_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="workspace-1"),
                body={"project_id": project_id},
            )
        )
        assert workspace_response.status == 201
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        assert isinstance(workspace_id, str) and workspace_id.startswith("workspace_")
        assert workspace_response.body["lifecycle"] == "identity_only"

        projects = await http.handle(HTTPRequest(method="GET", path="/api/v1/projects"))
        workspaces = await http.handle(HTTPRequest(method="GET", path="/api/v1/workspaces"))
        assert_page(projects.body, total=1)
        assert_page(workspaces.body, total=1)

        loaded = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/workspaces/{workspace_id}")
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["project_id"] == project_id

    asyncio.run(scenario())


def test_task_create_read_list_and_duplicate_create_are_idempotent() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        first = await _create_task(http, key="task-create-1")
        second = await _create_task(http, key="task-create-1")
        assert first["id"] == second["id"]

        task_id = first["id"]
        assert isinstance(task_id, str)
        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}"))
        assert loaded.status == 200
        assert loaded.body == first

        listed = await http.handle(HTTPRequest(method="GET", path="/api/v1/tasks"))
        items = assert_page(listed.body, total=1)
        assert isinstance(items[0], dict)
        assert items[0]["id"] == task_id

    asyncio.run(scenario())


def test_task_cancel_and_retry_commands_delegate_to_kernel() -> None:
    async def scenario() -> None:
        control_plane, kernel, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)

        cancelled_task = await _create_task(http, key="cancel-create", title="Cancel")
        cancel_id = cancelled_task["id"]
        assert isinstance(cancel_id, str)
        cancelled = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{cancel_id}:cancel",
                headers=api_headers(idempotency_key="cancel-command"),
            )
        )
        assert cancelled.status == 200
        assert isinstance(cancelled.body, dict)
        assert cancelled.body["status"] == "cancelled"

        task = await _create_task(http, key="retry-create", title="Retry")
        task_id = task["id"]
        assert isinstance(task_id, str)
        queued = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="retry-queue"),
            )
        )
        assert queued.status == 200
        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="retry-start"),
            )
        )
        assert started.status == 200
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)
        await kernel.record_run_outcome(
            idempotency_key="retry-fail-run",
            task_id=task_id,
            run_id=run_id,
            status=RunStatus.FAILED,
        )

        retried = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:retry",
                headers=api_headers(idempotency_key="retry-command"),
            )
        )
        retried_again = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:retry",
                headers=api_headers(idempotency_key="retry-command"),
            )
        )
        assert retried.status == 200
        assert retried.body == retried_again.body
        assert isinstance(retried.body, dict)
        assert retried.body["attempt"] == 2

    asyncio.run(scenario())


def test_invalid_lifecycle_transition_is_canonical_conflict() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        task = await _create_task(http, key="invalid-create")
        task_id = task["id"]
        assert isinstance(task_id, str)
        first = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="queue-1"),
            )
        )
        assert first.status == 200
        second = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="queue-2"),
            )
        )
        assert_error_envelope(second, code="conflict", status=409)

    asyncio.run(scenario())


def test_run_list_read_and_status_filter() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        task = await _create_task(http, key="run-create")
        task_id = task["id"]
        assert isinstance(task_id, str)
        await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="run-queue"),
            )
        )
        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="run-start"),
            )
        )
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)

        nested = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}/runs"))
        assert_page(nested.body, total=1)
        filtered = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/runs",
                query={"filter[status]": "running"},
            )
        )
        assert_page(filtered.body, total=1)
        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}"))
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["attempt"] == 1
        assert loaded.body["started_at"] is not None

    asyncio.run(scenario())


def test_pagination_filtering_sorting_search_and_fields() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        for index, title in enumerate(("Zulu", "Alpha", "Beta")):
            await _create_task(http, key=f"page-{index}", title=title)

        first = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "limit": "2",
                    "sort": "title",
                    "direction": "asc",
                    "fields": "title,status",
                },
            )
        )
        items = assert_page(first.body, total=3)
        assert [item["title"] for item in items if isinstance(item, dict)] == ["Alpha", "Beta"]
        assert isinstance(first.body, dict)
        cursor = first.body["next_cursor"]
        assert isinstance(cursor, str)

        second = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"limit": "2", "cursor": cursor, "sort": "title"},
            )
        )
        assert isinstance(second.body, dict)
        assert second.body["next_cursor"] is None

        searched = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"q": "Zulu", "filter[status]": "draft"},
            )
        )
        assert_page(searched.body, total=1)

    asyncio.run(scenario())


def test_malformed_request_and_content_type_validation() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        missing_content_type = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={"idempotency-key": "bad-content-type"},
                body={"title": "Task"},
            )
        )
        assert_error_envelope(
            missing_content_type,
            code="unsupported_media_type",
            status=415,
        )

        malformed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=api_headers(idempotency_key="bad-body"),
                body={"title": "Task", "owner_type": "user", "owner_id": "test"},
            )
        )
        assert_error_envelope(malformed, code="invalid_request", status=400)
        assert isinstance(malformed.body, dict)
        details = malformed.body.get("details")
        assert isinstance(details, dict)
        assert details["field"] == "objective"

    asyncio.run(scenario())


def test_unsupported_api_version_is_explicit() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        response = await ControlPlaneHTTP(control_plane).handle(
            HTTPRequest(method="GET", path="/api/v2/tasks")
        )
        assert_error_envelope(response, code="unsupported_api_version", status=400)
        assert isinstance(response.body, dict)
        details = response.body.get("details")
        assert isinstance(details, dict)
        assert details["supported_versions"] == ["v1"]

    asyncio.run(scenario())


def test_provider_error_mapping_does_not_leak_private_exception_types() -> None:
    async def scenario() -> None:
        authorization = FakeAuthorizationProvider(
            failure=FakeFailure(
                code=ErrorCode.BACKEND_ERROR,
                message="authorization backend failed",
                retryable=True,
            )
        )
        control_plane, _, _, _ = _stack(authorization=authorization)
        response = await ControlPlaneHTTP(control_plane).handle(
            HTTPRequest(method="GET", path="/api/v1/tasks")
        )
        assert_error_envelope(response, code="backend_error", status=502)
        serialized = json.dumps(response.body)
        assert "FakeFailure" not in serialized
        assert "fake-auth" not in serialized

    asyncio.run(scenario())


def test_timeline_query_preserves_correlation_and_can_filter_run_events() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack()
        http = ControlPlaneHTTP(control_plane)
        task = await _create_task(http, key="timeline-create")
        task_id = task["id"]
        assert isinstance(task_id, str)
        await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=api_headers(idempotency_key="timeline-queue"),
            )
        )
        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="timeline-start"),
            )
        )
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)

        timeline = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}/timeline")
        )
        items = assert_page(timeline.body)
        assert items
        assert all(
            isinstance(item, dict) and item.get("correlation_id") == task_id for item in items
        )

        run_events = await http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/tasks/{task_id}/timeline",
                query={"filter[subject_id]": run_id},
            )
        )
        run_event_items = assert_page(run_events.body)
        assert run_event_items
        assert all(
            isinstance(item, dict) and item.get("subject_id") == run_id for item in run_event_items
        )

    asyncio.run(scenario())


def test_live_task_update_stream_uses_canonical_event_payloads() -> None:
    async def scenario() -> None:
        control_plane, _, _, live_events = _stack(live=True)
        assert live_events is not None
        http = ControlPlaneHTTP(control_plane)
        task = await _create_task(http, key="stream-create")
        task_id = task["id"]
        assert isinstance(task_id, str)

        stream = await control_plane.subscribe_task_events(_context(), task_id)
        events = [event async for event in stream]
        assert events
        assert events[0]["event_type"] == "task.created"
        assert events[0]["correlation_id"] == task_id
        assert live_events.subscribe_calls

        messages: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        app = ControlPlaneASGI(http)
        await app(
            {
                "type": "http",
                "method": "GET",
                "path": f"/api/v1/tasks/{task_id}/events/stream",
                "query_string": b"",
                "headers": [],
            },
            receive,
            send,
        )
        bodies = b"".join(
            message.get("body", b"")
            for message in messages
            if message.get("type") == "http.response.body"
        )
        assert b"platform.event" in bodies
        assert b"task.created" in bodies
        assert b"fake-events" not in bodies

    asyncio.run(scenario())


def test_request_and_correlation_id_propagation() -> None:
    async def scenario() -> None:
        authorization = FakeAuthorizationProvider()
        control_plane, _, _, _ = _stack(authorization=authorization)
        response = await ControlPlaneHTTP(control_plane).handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=api_headers(
                    idempotency_key="correlation-create",
                    request_id="request-123",
                    correlation_id="correlation-123",
                ),
                body={
                    "title": "Correlation",
                    "objective": "Propagate request context",
                    "owner_type": "user",
                    "owner_id": "test",
                },
            )
        )
        assert response.status == 201
        assert response.headers["x-request-id"] == "request-123"
        assert response.headers["x-correlation-id"] == "correlation-123"
        assert authorization.calls
        assert authorization.calls[0].context.correlation_id == "correlation-123"

    asyncio.run(scenario())


def test_core_api_starts_without_future_optional_subsystems() -> None:
    async def scenario() -> None:
        control_plane, _, _, _ = _stack(live=False, authorization=None)
        http = ControlPlaneHTTP(control_plane)
        health = await http.handle(HTTPRequest(method="GET", path="/api/v1/health"))
        assert health.status == 200
        assert isinstance(health.body, dict)
        assert health.body["ready"] is True
        task = await _create_task(http, key="core-only-create")
        assert task["status"] == "draft"

    asyncio.run(scenario())


def test_openapi_is_current_scope_only_and_documents_evolution() -> None:
    spec = build_openapi()
    assert spec["openapi"] == "3.1.0"
    paths = spec["paths"]
    for resource in (
        "projects",
        "workspaces",
        "tasks",
        "runs",
        "plans",
        "steps",
        "artifacts",
        "results",
    ):
        assert f"/api/v1/{resource}" in paths
    assert "/api/v1/tasks/{task_id}/timeline" in paths
    assert "/api/v1/tasks/{task_id}/events/stream" in paths
    for extension_resource in (
        "agents",
        "models",
        "tools",
        "nodes",
        "automations",
        "evaluations",
        "plugins",
    ):
        assert f"/api/v1/{extension_resource}" in paths
    assert spec["x-evolution-policy"]["breaking_changes"] == "require a new major path namespace"
