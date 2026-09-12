from __future__ import annotations

import asyncio
import json

from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class EventSearchAuthorization(FakeAuthorizationProvider):
    def __init__(self, *, hidden_project_id: str | None = None) -> None:
        super().__init__()
        self.hidden_project_id = hidden_project_id

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if (
            request.action == "event:list"
            and self.hidden_project_id is not None
            and request.context.project_id == self.hidden_project_id
        ):
            return AuthorizationDecision(allowed=False, reason="event-project-hidden")
        return AuthorizationDecision(allowed=True, reason="event-visible")


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }


def _stack(
    authorization: EventSearchAuthorization,
) -> tuple[ControlPlane, ControlPlaneHTTP, PlatformKernel, InMemoryKernelRepository]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
    )
    return control_plane, ControlPlaneHTTP(control_plane), kernel, repository


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


def test_canonical_events_use_safe_global_search_metadata_without_payload_text() -> None:
    async def scenario() -> None:
        authorization = EventSearchAuthorization()
        control_plane, http, kernel, repository = _stack(authorization)
        project_id = new_id("project")
        payload_secret = "event-payload-secret-45-should-not-be-searchable"
        task = await kernel.create_task(
            idempotency_key="event-search-visible-create",
            title=payload_secret,
            objective="Keep canonical Event payload out of global Search text",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        )
        events = await repository.read_events(task.task_id)
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "task.created"
        assert event.payload["title"] == payload_secret

        by_type = await _search(http, type="event", q="task.created")
        assert by_type["total"] == 1
        result = _items(by_type)[0]
        assert result["resource_type"] == "event"
        assert result["resource_id"] == event.id
        assert result["title"] == f"task.created for task {task.task_id}"
        assert result["project_id"] == project_id
        assert result["owner_type"] == "user"
        assert result["owner_id"] == "alice"
        assert result["updated_at"] == event.occurred_at.isoformat()
        assert result["canonical_ref"] == f"/api/v1/tasks/{task.task_id}/timeline"
        assert result["provenance"] == {"indexed_from": "canonical-event-repository"}

        exact = await _search(http, type="event", id=event.id)
        assert exact["total"] == 1
        assert _items(exact)[0]["resource_id"] == event.id

        by_subject = await _search(http, type="event", q=task.task_id)
        assert by_subject["total"] == 1
        by_project = await _search(http, type="event", project_id=project_id)
        assert by_project["total"] == 1
        by_time = await _search(
            http,
            type="event",
            updated_after=event.occurred_at.isoformat(),
            updated_before=event.occurred_at.isoformat(),
        )
        assert by_time["total"] == 1

        payload_query = await _search(http, type="event", q=payload_secret)
        assert payload_query["total"] == 0
        serialized = json.dumps(by_type, sort_keys=True)
        assert payload_secret not in serialized
        assert "payload" not in serialized

        telemetry = await _search(http, type="telemetry")
        assert telemetry["total"] == 0

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt >= 2
        assert any(
            call.action == "event:list"
            and call.resource_ref == task.task_id
            and call.context.project_id == project_id
            for call in authorization.calls
        )

    asyncio.run(scenario())


def test_event_search_hides_unauthorized_task_events_from_counts_ids_and_project_filters() -> None:
    async def scenario() -> None:
        visible_project_id = new_id("project")
        hidden_project_id = new_id("project")
        authorization = EventSearchAuthorization(hidden_project_id=hidden_project_id)
        _, http, kernel, repository = _stack(authorization)

        visible_task = await kernel.create_task(
            idempotency_key="event-search-visible",
            title="Visible event task",
            objective="Visible event",
            owner_type="user",
            owner_id="alice",
            project_id=visible_project_id,
        )
        hidden_task = await kernel.create_task(
            idempotency_key="event-search-hidden",
            title="Hidden event task",
            objective="Hidden event",
            owner_type="user",
            owner_id="bob",
            project_id=hidden_project_id,
        )
        visible_event = (await repository.read_events(visible_task.task_id))[0]
        hidden_event = (await repository.read_events(hidden_task.task_id))[0]

        common_type = await _search(http, type="event", q="task.created")
        assert common_type["total"] == 1
        assert _items(common_type)[0]["resource_id"] == visible_event.id

        hidden_exact = await _search(http, type="event", id=hidden_event.id)
        hidden_project = await _search(http, type="event", project_id=hidden_project_id)
        hidden_subject = await _search(http, type="event", q=hidden_task.task_id)

        assert hidden_exact["total"] == 0
        assert hidden_project["total"] == 0
        assert hidden_subject["total"] == 0

        serialized = json.dumps(
            {
                "exact": hidden_exact,
                "project": hidden_project,
                "subject": hidden_subject,
            },
            sort_keys=True,
        )
        assert hidden_event.id not in serialized
        assert hidden_task.task_id not in serialized
        assert hidden_project_id not in serialized
        assert "bob" not in serialized

        denied_calls = [
            call
            for call in authorization.calls
            if call.action == "event:list"
            and call.resource_ref == hidden_task.task_id
            and call.context.project_id == hidden_project_id
        ]
        assert denied_calls

    asyncio.run(scenario())
