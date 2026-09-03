from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _headers(key: str | None = None) -> dict[str, str]:
    headers = {
        "content-type": "application/json",
        "x-principal-ref": "user:test",
        "x-owner-type": "user",
        "x-owner-id": "test",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _stack() -> ControlPlaneHTTP:
    events = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=events,
    )
    return ControlPlaneHTTP(ControlPlane(kernel=kernel, events=events))


async def _create_project(http: ControlPlaneHTTP) -> str:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=_headers("search-task-project"),
            body={"name": "Search task management", "owner_type": "user", "owner_id": "test"},
        )
    )
    assert response.status == 201, response.body
    assert isinstance(response.body, dict)
    project_id = response.body["id"]
    assert isinstance(project_id, str)
    return project_id


async def _create_task(
    http: ControlPlaneHTTP,
    *,
    key: str,
    title: str,
    project_id: str,
    **planning: Any,
) -> dict[str, Any]:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=_headers(key),
            body={
                "title": title,
                "objective": f"Objective for {title}",
                "owner_type": "user",
                "owner_id": "test",
                "project_id": project_id,
                **planning,
            },
        )
    )
    assert response.status == 201, response.body
    assert isinstance(response.body, dict)
    return response.body


async def _search(http: ControlPlaneHTTP, **query: str):
    response = await http.handle(
        HTTPRequest(method="GET", path="/api/v1/search", query={"type": "task", **query})
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _ids(page: dict[str, Any]) -> list[str]:
    items = page["items"]
    assert isinstance(items, list)
    return [item["resource_id"] for item in items]


def test_search_consumes_canonical_task_management_projection_and_filters() -> None:
    async def scenario() -> None:
        http = _stack()
        project_id = await _create_project(http)
        now = datetime.now(UTC)
        agent_id = new_id("agent")

        prerequisite = await _create_task(
            http,
            key="search-prerequisite",
            title="Prerequisite",
            project_id=project_id,
        )
        prerequisite_id = prerequisite["id"]
        assert isinstance(prerequisite_id, str)

        managed = await _create_task(
            http,
            key="search-managed",
            title="Managed release task",
            project_id=project_id,
            priority="urgent",
            due_at=(now + timedelta(hours=2)).isoformat(),
            responsibility={"kind": "team", "id": "release-team"},
            agent_assignment={
                "kind": "agent",
                "id": agent_id,
                "revision": 1,
                "required": True,
            },
            labels=["release", "searchable"],
            dependencies=[{"task_id": prerequisite_id, "kind": "depends_on"}],
        )
        managed_id = managed["id"]
        assert isinstance(managed_id, str)

        overdue = await _create_task(
            http,
            key="search-overdue",
            title="Overdue task",
            project_id=project_id,
            priority="high",
            due_at=(now - timedelta(hours=2)).isoformat(),
        )
        overdue_id = overdue["id"]
        assert isinstance(overdue_id, str)

        unassigned = await _create_task(
            http,
            key="search-unassigned",
            title="Unassigned task",
            project_id=project_id,
            priority="normal",
            due_at=(now + timedelta(days=3)).isoformat(),
        )
        unassigned_id = unassigned["id"]
        assert isinstance(unassigned_id, str)

        assert _ids(await _search(http, priority="urgent")) == [managed_id]
        assert _ids(
            await _search(
                http,
                due_after=now.isoformat(),
                due_before=(now + timedelta(days=1)).isoformat(),
            )
        ) == [managed_id]
        assert _ids(await _search(http, assignment_state="assigned")) == [managed_id]
        assert set(_ids(await _search(http, assignment_state="unassigned"))) == {
            prerequisite_id,
            overdue_id,
            unassigned_id,
        }
        assert _ids(await _search(http, responsible_id="release-team")) == [managed_id]
        assert _ids(await _search(http, agent_assignment_id=agent_id)) == [managed_id]
        assert _ids(await _search(http, blocked="true")) == [managed_id]
        assert overdue_id in _ids(await _search(http, overdue="true"))
        assert _ids(await _search(http, dependency_id=prerequisite_id)) == [managed_id]
        assert _ids(await _search(http, tag="release")) == [managed_id]

        by_relationship_keyword = await _search(http, q="release-team")
        assert _ids(by_relationship_keyword) == [managed_id]
        assert by_relationship_keyword["items"][0]["matched_fields"] == ["keywords"]

        exact = await _search(http, id=managed_id)
        assert exact["total"] == 1
        result = exact["items"][0]
        assert result["resource_id"] == managed_id
        assert result["canonical_ref"] == f"/api/v1/tasks/{managed_id}"
        assert result["tags"] == ["release", "searchable"]

    asyncio.run(scenario())


def test_task_management_search_rejects_invalid_filter_values() -> None:
    async def scenario() -> None:
        http = _stack()
        now = datetime.now(UTC)

        invalid_queries = (
            {"priority": "critical"},
            {"assignment_state": "someone"},
            {"blocked": "maybe"},
            {"overdue": "maybe"},
            {
                "due_after": (now + timedelta(hours=2)).isoformat(),
                "due_before": now.isoformat(),
            },
            {"dependency_id": "not-a-task-id"},
        )
        for query in invalid_queries:
            response = await http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/search",
                    query={"type": "task", **query},
                )
            )
            assert response.status == 400, (query, response.body)
            assert isinstance(response.body, dict)
            assert response.body["code"] == "invalid_request"

    asyncio.run(scenario())
