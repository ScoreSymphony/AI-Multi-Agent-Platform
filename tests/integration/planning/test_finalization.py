from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider


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


def _stack(tmp_path: Path | None = None):
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    workspace_provider = None
    if tmp_path is not None:
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        workspace_provider = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspace_provider,
    )
    return ControlPlaneHTTP(control_plane), kernel, repository


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


async def _management_update(
    http: ControlPlaneHTTP,
    *,
    key: str,
    task_id: str,
    changes: dict[str, Any],
):
    return await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/commands/task-management.update",
            headers=_headers(key),
            body={"resource_ref": task_id, **changes},
        )
    )


def test_terminal_tasks_accept_planning_metadata_without_reopening_lifecycle() -> None:
    async def scenario() -> None:
        http, kernel, repository = _stack()

        succeeded = await _create_task(http, key="terminal-succeeded", title="Succeeded")
        succeeded_id = succeeded["id"]
        assert isinstance(succeeded_id, str)
        await kernel.ready_task(idempotency_key="terminal-ready", task_id=succeeded_id)
        run = await kernel.start_task(idempotency_key="terminal-start", task_id=succeeded_id)
        await kernel.record_run_outcome(
            idempotency_key="terminal-run-succeeded",
            task_id=succeeded_id,
            run_id=run.run_id,
            status=RunStatus.SUCCEEDED,
        )

        archived = await _management_update(
            http,
            key="terminal-archive",
            task_id=succeeded_id,
            changes={"archived": True, "hidden": True},
        )
        assert archived.status == 200
        assert isinstance(archived.body, dict)
        assert archived.body["status"] == "succeeded"
        assert archived.body["archived"] is True
        assert archived.body["hidden"] is True

        cancelled = await _create_task(http, key="terminal-cancelled", title="Cancelled")
        cancelled_id = cancelled["id"]
        assert isinstance(cancelled_id, str)
        await kernel.cancel_task(idempotency_key="terminal-cancel", task_id=cancelled_id)
        hidden = await _management_update(
            http,
            key="terminal-hide",
            task_id=cancelled_id,
            changes={"hidden": True},
        )
        assert hidden.status == 200
        assert isinstance(hidden.body, dict)
        assert hidden.body["status"] == "cancelled"
        assert hidden.body["hidden"] is True

        succeeded_events = await repository.read_events(succeeded_id)
        assert succeeded_events[-1].event_type == "task.updated"
        assert succeeded_events[-1].payload["source"] == "control-plane"

    asyncio.run(scenario())


def test_default_queue_visibility_for_archived_and_hidden_tasks() -> None:
    async def scenario() -> None:
        http, _, _ = _stack()
        active = await _create_task(http, key="visibility-active", title="Active")
        archived = await _create_task(
            http,
            key="visibility-archived",
            title="Archived",
            archived=True,
        )
        hidden = await _create_task(
            http,
            key="visibility-hidden",
            title="Hidden",
            hidden=True,
        )

        default_queue = await http.handle(HTTPRequest(method="GET", path="/api/v1/tasks"))
        assert default_queue.status == 200
        assert isinstance(default_queue.body, dict)
        assert default_queue.body["total"] == 1
        default_items = default_queue.body["items"]
        assert isinstance(default_items, list)
        assert default_items[0]["id"] == active["id"]

        archived_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"filter[archived]": "true"},
            )
        )
        assert archived_queue.status == 200
        assert isinstance(archived_queue.body, dict)
        assert archived_queue.body["total"] == 1
        archived_items = archived_queue.body["items"]
        assert isinstance(archived_items, list)
        assert archived_items[0]["id"] == archived["id"]

        hidden_queue = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={"filter[hidden]": "true"},
            )
        )
        assert hidden_queue.status == 200
        assert isinstance(hidden_queue.body, dict)
        assert hidden_queue.body["total"] == 1
        hidden_items = hidden_queue.body["items"]
        assert isinstance(hidden_items, list)
        assert hidden_items[0]["id"] == hidden["id"]

    asyncio.run(scenario())


def test_task_workspace_reference_uses_canonical_workspace_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, _, _ = _stack(tmp_path)

        project_a = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers("workspace-project-a"),
                body={"name": "Project A", "owner_type": "user", "owner_id": "test"},
            )
        )
        project_b = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers("workspace-project-b"),
                body={"name": "Project B", "owner_type": "user", "owner_id": "test"},
            )
        )
        assert project_a.status == 201 and project_b.status == 201
        assert isinstance(project_a.body, dict) and isinstance(project_b.body, dict)
        project_a_id = project_a.body["id"]
        project_b_id = project_b.body["id"]
        assert isinstance(project_a_id, str) and isinstance(project_b_id, str)

        workspace = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=_headers("workspace-create"),
                body={"project_id": project_a_id},
            )
        )
        assert workspace.status == 201, workspace.body
        assert isinstance(workspace.body, dict)
        workspace_id = workspace.body["id"]
        assert isinstance(workspace_id, str)

        same_project = await _create_task(
            http,
            key="workspace-same-project",
            title="Same project workspace",
            project_id=project_a_id,
            workspace_id=workspace_id,
        )
        assert same_project["workspace_id"] == workspace_id

        cross_project = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers("workspace-cross-project"),
                body={
                    "title": "Cross project workspace",
                    "objective": "Must be rejected",
                    "owner_type": "user",
                    "owner_id": "test",
                    "project_id": project_b_id,
                    "workspace_id": workspace_id,
                },
            )
        )
        assert cross_project.status == 400
        assert isinstance(cross_project.body, dict)
        assert cross_project.body["code"] == "invalid_request"
        assert "workspace must belong to the task project" in str(cross_project.body["message"])

    asyncio.run(scenario())
