from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import RunStatus, new_id
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


def _stack(
    *,
    repository: InMemoryKernelRepository | None = None,
    orchestrator: FakeOrchestrator | None = None,
    lifecycle: FakeLifecycleBackend | None = None,
) -> tuple[ControlPlaneHTTP, PlatformKernel, InMemoryKernelRepository]:
    event_repository = repository or InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=orchestrator or FakeOrchestrator(),
        lifecycle=lifecycle or FakeLifecycleBackend(),
        repository=event_repository,
    )
    control_plane = ControlPlane(kernel=kernel, events=event_repository)
    return ControlPlaneHTTP(control_plane), kernel, event_repository


async def _create_task(
    http: ControlPlaneHTTP,
    *,
    key: str,
    title: str,
    project_id: str | None = None,
    **planning: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "title": title,
        "objective": f"Objective for {title}",
        "owner_type": "user",
        "owner_id": "test",
        **planning,
    }
    if project_id is not None:
        body["project_id"] = project_id
    response = await http.handle(
        HTTPRequest(method="POST", path="/api/v1/tasks", headers=_headers(key), body=body)
    )
    assert response.status == 201, response.body
    assert isinstance(response.body, dict)
    return response.body


async def _command(
    http: ControlPlaneHTTP,
    *,
    key: str,
    resource_ref: str,
    payload: dict[str, Any],
):
    return await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/task-management.update",
            headers=_headers(key),
            body={"resource_ref": resource_ref, **payload},
        )
    )


def test_priority_update_and_absolute_upcoming_range_query() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        now = datetime.now(UTC)
        past = await _create_task(
            http,
            key="hardening-past",
            title="Past",
            priority="low",
            due_at=(now - timedelta(hours=1)).isoformat(),
        )
        upcoming = await _create_task(
            http,
            key="hardening-upcoming",
            title="Upcoming",
            priority="normal",
            due_at=(now + timedelta(minutes=30)).isoformat(),
        )
        await _create_task(http, key="hardening-undated", title="Undated")

        upcoming_id = upcoming["id"]
        assert isinstance(upcoming_id, str)
        updated = await _command(
            http,
            key="hardening-priority-update",
            resource_ref=upcoming_id,
            payload={"priority": "urgent"},
        )
        assert updated.status == 200
        assert isinstance(updated.body, dict)
        assert updated.body["priority"] == "urgent"

        ordered = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"sort": "priority", "direction": "desc"},
            )
        )
        assert ordered.status == 200
        assert isinstance(ordered.body, dict)
        items = ordered.body["items"]
        assert isinstance(items, list)
        assert items[0]["id"] == upcoming_id

        ranged = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[due_after]": now.isoformat(),
                    "filter[due_before]": (now + timedelta(hours=1)).isoformat(),
                },
            )
        )
        assert ranged.status == 200
        assert isinstance(ranged.body, dict)
        assert ranged.body["total"] == 1
        ranged_items = ranged.body["items"]
        assert isinstance(ranged_items, list)
        assert ranged_items[0]["id"] == upcoming_id

        overdue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"filter[overdue]": "true"},
            )
        )
        assert overdue.status == 200
        assert isinstance(overdue.body, dict)
        overdue_items = overdue.body["items"]
        assert isinstance(overdue_items, list)
        assert [item["id"] for item in overdue_items] == [past["id"]]

        invalid = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[due_after]": (now + timedelta(hours=2)).isoformat(),
                    "filter[due_before]": now.isoformat(),
                },
            )
        )
        assert invalid.status == 400
        assert isinstance(invalid.body, dict)
        assert invalid.body["code"] == "invalid_request"

    asyncio.run(scenario())


def test_project_team_agent_and_unassigned_queue_filters() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        project = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers("hardening-project"),
                body={"name": "Hardening", "owner_type": "user", "owner_id": "test"},
            )
        )
        assert project.status == 201
        assert isinstance(project.body, dict)
        project_id = project.body["id"]
        assert isinstance(project_id, str)

        team_id = new_id("team")
        assigned = await _create_task(
            http,
            key="hardening-assigned",
            title="Assigned",
            project_id=project_id,
            responsibility={"kind": "team", "id": "product"},
            agent_assignment={
                "kind": "agent_team",
                "id": team_id,
                "revision": 2,
                "required": True,
                "policy_ref": "policy:team",
            },
        )
        unassigned = await _create_task(
            http,
            key="hardening-unassigned",
            title="Unassigned",
            project_id=project_id,
        )

        project_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"filter[project_id]": project_id},
            )
        )
        assert project_queue.status == 200
        assert isinstance(project_queue.body, dict)
        assert project_queue.body["total"] == 2

        team_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[project_id]": project_id,
                    "filter[responsible_type]": "team",
                    "filter[responsible_id]": "product",
                },
            )
        )
        assert team_queue.status == 200
        assert isinstance(team_queue.body, dict)
        assert team_queue.body["total"] == 1
        team_items = team_queue.body["items"]
        assert isinstance(team_items, list)
        assert team_items[0]["id"] == assigned["id"]

        agent_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[project_id]": project_id,
                    "filter[agent_assignment_type]": "agent_team",
                    "filter[agent_assignment_id]": team_id,
                },
            )
        )
        assert agent_queue.status == 200
        assert isinstance(agent_queue.body, dict)
        assert agent_queue.body["total"] == 1

        unassigned_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[project_id]": project_id,
                    "filter[assignment_state]": "unassigned",
                },
            )
        )
        assert unassigned_queue.status == 200
        assert isinstance(unassigned_queue.body, dict)
        assert unassigned_queue.body["total"] == 1
        unassigned_items = unassigned_queue.body["items"]
        assert isinstance(unassigned_items, list)
        assert unassigned_items[0]["id"] == unassigned["id"]

    asyncio.run(scenario())


def test_agent_and_agent_team_reference_shapes_are_both_validated() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        task = await _create_task(http, key="hardening-agent-create", title="Agent shapes")
        task_id = task["id"]
        assert isinstance(task_id, str)

        agent = await _command(
            http,
            key="hardening-agent",
            resource_ref=task_id,
            payload={"agent_assignment": {"kind": "agent", "id": new_id("agent")}},
        )
        assert agent.status == 200

        team_id = new_id("team")
        team = await _command(
            http,
            key="hardening-agent-team",
            resource_ref=task_id,
            payload={
                "agent_assignment": {
                    "kind": "agent_team",
                    "id": team_id,
                    "revision": 1,
                }
            },
        )
        assert team.status == 200
        assert isinstance(team.body, dict)
        assert team.body["agent_assignment_type"] == "agent_team"
        assert team.body["agent_assignment_id"] == team_id

        invalid_team = await _command(
            http,
            key="hardening-invalid-agent-team",
            resource_ref=task_id,
            payload={"agent_assignment": {"kind": "agent_team", "id": new_id("agent")}},
        )
        assert invalid_team.status == 400
        assert isinstance(invalid_team.body, dict)
        assert invalid_team.body["code"] == "invalid_request"

    asyncio.run(scenario())


def test_failed_prerequisite_is_reported_as_failed_dependency() -> None:
    async def scenario() -> None:
        http, kernel, _ = _stack()
        prerequisite = await _create_task(
            http,
            key="hardening-failed-pre",
            title="Failed prerequisite",
        )
        dependent = await _create_task(
            http,
            key="hardening-failed-dep",
            title="Dependent",
        )
        prerequisite_id = prerequisite["id"]
        dependent_id = dependent["id"]
        assert isinstance(prerequisite_id, str) and isinstance(dependent_id, str)

        await kernel.ready_task(idempotency_key="hardening-pre-ready", task_id=prerequisite_id)
        run = await kernel.start_task(
            idempotency_key="hardening-pre-start", task_id=prerequisite_id
        )
        await kernel.record_run_outcome(
            idempotency_key="hardening-pre-failed",
            task_id=prerequisite_id,
            run_id=run.run_id,
            status=RunStatus.FAILED,
        )

        linked = await _command(
            http,
            key="hardening-failed-link",
            resource_ref=dependent_id,
            payload={"dependencies": [{"task_id": prerequisite_id, "kind": "depends_on"}]},
        )
        assert linked.status == 200
        assert isinstance(linked.body, dict)
        assert linked.body["failed_dependency_ids"] == [prerequisite_id]
        assert linked.body["blocking_task_ids"] == [prerequisite_id]
        assert linked.body["effective_blocking_reason"] == "prerequisite_failed_or_cancelled"
        assert linked.body["eligible"] is False

    asyncio.run(scenario())


def test_provider_and_orchestrator_replacement_leave_task_metadata_unchanged() -> None:
    class ReplacementOrchestrator(FakeOrchestrator):
        pass

    class ReplacementLifecycleProvider(FakeLifecycleBackend):
        pass

    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        first_http, _, _ = _stack(repository=repository)
        task = await _create_task(
            first_http,
            key="hardening-replacement-create",
            title="Replace providers",
            priority="urgent",
            labels=["persistent"],
            blocking_reason="review-required",
        )
        task_id = task["id"]
        assert isinstance(task_id, str)

        replacement_http, _, _ = _stack(
            repository=repository,
            orchestrator=ReplacementOrchestrator(),
            lifecycle=ReplacementLifecycleProvider(),
        )
        loaded = await replacement_http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/tasks/{task_id}")
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["priority"] == "urgent"
        assert loaded.body["labels"] == ["persistent"]
        assert loaded.body["blocking_reason"] == "review-required"

    asyncio.run(scenario())
