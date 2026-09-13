from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.data import LocalFileProvider
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


def _stack(tmp_path: Path) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    workspaces = SqliteWorkspaceProvider(
        tmp_path / "materializations",
        files,
        tmp_path / "workspaces.sqlite",
    )
    return ControlPlaneHTTP(
        ControlPlane(
            kernel=kernel,
            events=repository,
            workspace_provider=workspaces,
        )
    )


def test_public_workspace_composition_preserves_issue88_upcoming_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        http = _stack(tmp_path)
        now = datetime.now(UTC)
        task = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers("issue88-composed-task"),
                body={
                    "title": "Composed upcoming task",
                    "objective": "Verify #37 composition preserves #88 queue queries",
                    "owner_type": "user",
                    "owner_id": "test",
                    "priority": "high",
                    "due_at": (now + timedelta(minutes=30)).isoformat(),
                },
            )
        )
        assert task.status == 201
        assert isinstance(task.body, dict)

        upcoming = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                query={
                    "filter[due_after]": now.isoformat(),
                    "filter[due_before]": (now + timedelta(hours=1)).isoformat(),
                },
            )
        )
        assert upcoming.status == 200
        assert isinstance(upcoming.body, dict)
        assert upcoming.body["total"] == 1
        items = upcoming.body["items"]
        assert isinstance(items, list)
        assert items[0]["id"] == task.body["id"]
        assert items[0]["priority"] == "high"

    asyncio.run(scenario())


def test_public_workspace_openapi_preserves_issue88_query_contract() -> None:
    specification = build_openapi()
    extension = specification["x-task-management"]
    assert extension["deadline_range_filters"] == {
        "due_after": "inclusive ISO 8601 lower bound",
        "due_before": "inclusive ISO 8601 upper bound",
    }
    assert "assignment_state" in extension["queue_filters"]
