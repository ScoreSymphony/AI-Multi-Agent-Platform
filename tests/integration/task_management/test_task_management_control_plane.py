from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_multi_agent_platform.contracts import AuthorizationDecision
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


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
    authorization: FakeAuthorizationProvider | None = None,
    repository: InMemoryKernelRepository | None = None,
) -> tuple[ControlPlaneHTTP, PlatformKernel, InMemoryKernelRepository]:
    event_repository = repository or InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=event_repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=event_repository,
        authorization=authorization,
    )
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
    command: str,
    resource_ref: str,
    payload: dict[str, Any],
):
    return await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(key),
            body={"resource_ref": resource_ref, **payload},
        )
    )


def test_priority_deadline_not_before_and_query_projection() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        now = datetime.now(UTC)
        low = await _create_task(
            http,
            key="low-create",
            title="Low",
            priority="low",
            due_at=(now - timedelta(hours=1)).isoformat(),
        )
        urgent = await _create_task(
            http,
            key="urgent-create",
            title="Urgent",
            priority="urgent",
            not_before=(now + timedelta(hours=1)).isoformat(),
            deadline_timezone="Europe/Berlin",
            labels=["release", "ops"],
        )
        assert low["priority"] == "low"
        assert low["overdue"] is True
        assert low["status"] == "draft"
        assert urgent["priority_rank"] == 40
        assert urgent["not_before_blocked"] is True
        assert urgent["blocked"] is True

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"sort": "priority", "direction": "desc"},
            )
        )
        assert listed.status == 200
        assert isinstance(listed.body, dict)
        items = listed.body["items"]
        assert isinstance(items, list)
        assert [item["priority"] for item in items] == ["urgent", "low"]

        overdue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"filter[overdue]": "true"},
            )
        )
        assert overdue.status == 200
        assert isinstance(overdue.body, dict)
        assert overdue.body["total"] == 1

        urgent_id = urgent["id"]
        assert isinstance(urgent_id, str)
        blocked_queue = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{urgent_id}:queue",
                headers=_headers("urgent-queue"),
            )
        )
        assert blocked_queue.status == 409
        assert isinstance(blocked_queue.body, dict)
        assert blocked_queue.body["code"] == "conflict"

    asyncio.run(scenario())


def test_responsibility_reassignment_and_agent_assignment_are_permission_neutral() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        task = await _create_task(http, key="assignment-create", title="Assignment")
        task_id = task["id"]
        assert isinstance(task_id, str)
        agent_id = new_id("agent")
        updated = await _command(
            http,
            key="assignment-update",
            command="task-management.update",
            resource_ref=task_id,
            payload={
                "responsibility": {"kind": "team", "id": "product"},
                "agent_assignment": {
                    "kind": "agent",
                    "id": agent_id,
                    "revision": 3,
                    "required": True,
                    "policy_ref": "policy:careful",
                },
            },
        )
        assert updated.status == 200
        assert isinstance(updated.body, dict)
        assert updated.body["responsible_id"] == "product"
        assert updated.body["agent_assignment_id"] == agent_id

        reassigned = await _command(
            http,
            key="assignment-reassign",
            command="task-management.update",
            resource_ref=task_id,
            payload={"responsibility": {"kind": "user", "id": "next-owner"}},
        )
        assert reassigned.status == 200
        assert isinstance(reassigned.body, dict)
        assert reassigned.body["responsible_type"] == "user"
        assert reassigned.body["responsible_id"] == "next-owner"

        invalid = await _command(
            http,
            key="assignment-invalid",
            command="task-management.update",
            resource_ref=task_id,
            payload={"agent_assignment": {"kind": "agent", "id": "provider-run-123"}},
        )
        assert invalid.status == 400
        assert isinstance(invalid.body, dict)
        assert invalid.body["code"] == "invalid_request"

    asyncio.run(scenario())


def test_dependency_satisfaction_cycle_cross_project_and_blocked_reason() -> None:
    async def scenario() -> None:
        http, kernel, _ = _stack()
        prerequisite = await _create_task(http, key="pre-create", title="Prerequisite")
        dependent = await _create_task(http, key="dep-create", title="Dependent")
        prerequisite_id = prerequisite["id"]
        dependent_id = dependent["id"]
        assert isinstance(prerequisite_id, str) and isinstance(dependent_id, str)

        linked = await _command(
            http,
            key="dep-link",
            command="task-management.update",
            resource_ref=dependent_id,
            payload={"dependencies": [{"task_id": prerequisite_id, "kind": "depends_on"}]},
        )
        assert linked.status == 200
        assert isinstance(linked.body, dict)
        assert linked.body["blocking_task_ids"] == [prerequisite_id]
        assert linked.body["eligible"] is False
        assert linked.body["effective_blocking_reason"] == "prerequisite_incomplete"

        await kernel.ready_task(idempotency_key="pre-ready", task_id=prerequisite_id)
        run = await kernel.start_task(idempotency_key="pre-start", task_id=prerequisite_id)
        await kernel.record_run_outcome(
            idempotency_key="pre-succeed-run",
            task_id=prerequisite_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
        )
        eligible = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/tasks/{dependent_id}")
        )
        assert eligible.status == 200
        assert isinstance(eligible.body, dict)
        assert eligible.body["blocking_task_ids"] == []
        assert eligible.body["eligible"] is True

        other = await _create_task(http, key="cycle-other", title="Cycle Other")
        other_id = other["id"]
        assert isinstance(other_id, str)
        first = await _command(
            http,
            key="cycle-first",
            command="task-management.update",
            resource_ref=dependent_id,
            payload={"dependencies": [{"task_id": other_id}]},
        )
        assert first.status == 200
        cycle = await _command(
            http,
            key="cycle-second",
            command="task-management.update",
            resource_ref=other_id,
            payload={"dependencies": [{"task_id": dependent_id}]},
        )
        assert cycle.status == 400
        assert isinstance(cycle.body, dict)
        assert "cycle" in str(cycle.body["message"]).lower()

        project_a = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers("project-a"),
                body={"name": "A", "owner_type": "user", "owner_id": "test"},
            )
        )
        project_b = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers("project-b"),
                body={"name": "B", "owner_type": "user", "owner_id": "test"},
            )
        )
        assert isinstance(project_a.body, dict) and isinstance(project_b.body, dict)
        task_a = await _create_task(
            http,
            key="task-a",
            title="Task A",
            project_id=project_a.body["id"],
        )
        task_b = await _create_task(
            http,
            key="task-b",
            title="Task B",
            project_id=project_b.body["id"],
        )
        cross = await _command(
            http,
            key="cross-project",
            command="task-management.update",
            resource_ref=task_a["id"],
            payload={"dependencies": [{"task_id": task_b["id"]}]},
        )
        assert cross.status == 400
        assert isinstance(cross.body, dict)
        assert "cross-project" in str(cross.body["message"])

    asyncio.run(scenario())


def test_cancelled_prerequisite_blocks_and_update_is_in_canonical_history() -> None:
    async def scenario() -> None:
        http, kernel, repository = _stack()
        prerequisite = await _create_task(http, key="cancel-pre", title="Cancelled prerequisite")
        dependent = await _create_task(http, key="cancel-dep", title="Blocked dependent")
        prerequisite_id = prerequisite["id"]
        dependent_id = dependent["id"]
        assert isinstance(prerequisite_id, str) and isinstance(dependent_id, str)
        await kernel.cancel_task(idempotency_key="cancel-pre-command", task_id=prerequisite_id)
        updated = await _command(
            http,
            key="cancel-dep-link",
            command="task-management.update",
            resource_ref=dependent_id,
            payload={"dependencies": [{"task_id": prerequisite_id}], "priority": "high"},
        )
        assert updated.status == 200
        assert isinstance(updated.body, dict)
        assert updated.body["failed_dependency_ids"] == [prerequisite_id]
        assert updated.body["effective_blocking_reason"] == "prerequisite_failed_or_cancelled"

        events = await repository.read_events(dependent_id)
        updates = [event for event in events if event.event_type == "task.updated"]
        assert len(updates) == 1
        metadata = updates[0].payload["metadata"]
        assert isinstance(metadata, Mapping)
        assert "task_management" in metadata

    asyncio.run(scenario())


def test_bulk_update_preflights_per_task_authorization() -> None:
    class SelectiveAuthorization(FakeAuthorizationProvider):
        denied_resource: str | None = None

        async def authorize(self, request):
            self.calls.append(request)
            denied = (
                request.action == "task:update-management"
                and request.resource_ref == self.denied_resource
            )
            return AuthorizationDecision(allowed=not denied, reason="selective")

    async def scenario() -> None:
        authorization = SelectiveAuthorization()
        http, _, _ = _stack(authorization=authorization)
        first = await _create_task(http, key="bulk-first", title="First")
        second = await _create_task(http, key="bulk-second", title="Second")
        first_id = first["id"]
        second_id = second["id"]
        assert isinstance(first_id, str) and isinstance(second_id, str)
        authorization.denied_resource = second_id

        response = await _command(
            http,
            key="bulk-update",
            command="task-management.bulk-update",
            resource_ref="tasks",
            payload={
                "updates": [
                    {"task_id": first_id, "changes": {"priority": "high"}},
                    {"task_id": second_id, "changes": {"priority": "urgent"}},
                ]
            },
        )
        assert response.status == 403

        first_after = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/tasks/{first_id}"))
        assert isinstance(first_after.body, dict)
        assert first_after.body["priority"] == "normal"

    asyncio.run(scenario())


def test_orchestrator_replacement_does_not_change_task_management_metadata() -> None:
    async def scenario() -> None:
        repository = InMemoryKernelRepository()
        first_http, _, _ = _stack(repository=repository)
        task = await _create_task(
            first_http,
            key="replace-create",
            title="Replaceable orchestration",
            priority="urgent",
            labels=["persistent"],
            blocking_reason="review-required",
        )
        task_id = task["id"]
        assert isinstance(task_id, str)

        replacement_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=repository,
        )
        replacement_http = ControlPlaneHTTP(
            ControlPlane(kernel=replacement_kernel, events=repository)
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
