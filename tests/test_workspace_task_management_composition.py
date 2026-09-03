from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_contract_helpers import api_headers

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


def test_workspace_and_task_management_share_the_public_control_plane(tmp_path: Path) -> None:
    async def scenario() -> None:
        http = _stack(tmp_path)
        project = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="composition-project"),
                body={
                    "name": "Composition project",
                    "owner_type": "user",
                    "owner_id": "contract-test",
                },
            )
        )
        assert project.status == 201
        assert isinstance(project.body, dict)
        project_id = project.body["id"]
        assert isinstance(project_id, str)

        workspace = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="composition-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "persistent_project",
                },
            )
        )
        assert workspace.status == 201
        assert isinstance(workspace.body, dict)
        assert workspace.body["lifecycle"] == "canonical"
        workspace_id = workspace.body["id"]
        assert isinstance(workspace_id, str)

        task = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=api_headers(idempotency_key="composition-task"),
                body={
                    "title": "Composition task",
                    "objective": "Verify #37 and #88 coexist",
                    "project_id": project_id,
                    "owner_type": "user",
                    "owner_id": "contract-test",
                },
            )
        )
        assert task.status == 201
        assert isinstance(task.body, dict)
        task_id = task.body["id"]
        assert isinstance(task_id, str)

        managed = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/task-management.update",
                headers=api_headers(idempotency_key="composition-management"),
                body={"resource_ref": task_id, "priority": "high"},
            )
        )
        assert managed.status == 200
        assert isinstance(managed.body, dict)
        assert managed.body["priority"] == "high"

        loaded_workspace = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/workspaces/{workspace_id}")
        )
        assert loaded_workspace.status == 200
        assert isinstance(loaded_workspace.body, dict)
        assert loaded_workspace.body["id"] == workspace_id

    asyncio.run(scenario())


def test_combined_openapi_exposes_workspace_run_and_task_management_contracts() -> None:
    specification = build_openapi()
    schemas = specification["components"]["schemas"]
    assert "Workspace" in schemas
    assert "workspace_snapshot_id" in schemas["Run"]["properties"]
    paths = specification["paths"]
    assert "/api/v1/commands/task-management.update" in paths
    assert "/api/v1/commands/task-management.bulk-update" in paths
