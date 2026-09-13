from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_contract_helpers import api_headers, assert_page

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    SqliteRunWorkspaceBindingRepository,
    SqliteWorkspaceProvider,
)


def _stack(
    tmp_path: Path,
) -> tuple[
    ControlPlaneHTTP,
    PlatformKernel,
    SqliteWorkspaceProvider,
    SqliteRunWorkspaceBindingRepository,
]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    metadata_db = tmp_path / "workspaces.sqlite"
    workspaces = SqliteWorkspaceProvider(
        tmp_path / "materializations",
        files,
        metadata_db,
    )
    bindings = SqliteRunWorkspaceBindingRepository(metadata_db)
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspaces,
        run_workspace_bindings=bindings,
    )
    return ControlPlaneHTTP(control_plane), kernel, workspaces, bindings


async def _project_workspace_task(
    http: ControlPlaneHTTP,
) -> tuple[str, str, str]:
    project_response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=api_headers(idempotency_key="project-create"),
            body={
                "name": "Run workspace project",
                "owner_type": "user",
                "owner_id": "workspace-user",
            },
        )
    )
    assert project_response.status == 201
    assert isinstance(project_response.body, dict)
    project_id = project_response.body["id"]
    assert isinstance(project_id, str)

    workspace_response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/workspaces",
            headers=api_headers(idempotency_key="workspace-create"),
            body={"project_id": project_id, "workspace_type": "isolated_run"},
        )
    )
    assert workspace_response.status == 201
    assert isinstance(workspace_response.body, dict)
    workspace_id = workspace_response.body["id"]
    snapshot_id = workspace_response.body["base_snapshot_id"]
    assert isinstance(workspace_id, str)
    assert isinstance(snapshot_id, str)

    task_response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=api_headers(idempotency_key="task-create"),
            body={
                "title": "Workspace-bound task",
                "objective": "Record exact workspace input",
                "owner_type": "user",
                "owner_id": "workspace-user",
                "project_id": project_id,
            },
        )
    )
    assert task_response.status == 201
    assert isinstance(task_response.body, dict)
    task_id = task_response.body["id"]
    assert isinstance(task_id, str)
    queued = await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:queue",
            headers=api_headers(idempotency_key="task-queue"),
        )
    )
    assert queued.status == 200
    return task_id, workspace_id, snapshot_id


def test_run_records_exact_workspace_snapshot_before_dispatch(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, _, provider, bindings = _stack(tmp_path)
        task_id, workspace_id, snapshot_id = await _project_workspace_task(http)
        snapshot = await provider.get_snapshot(snapshot_id)
        request = HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:start",
            headers=api_headers(idempotency_key="task-start"),
            body={
                "workspace_id": workspace_id,
                "workspace_snapshot_id": snapshot_id,
            },
        )
        started = await http.handle(request)
        repeated = await http.handle(request)
        assert started.status == repeated.status == 200
        assert isinstance(started.body, dict)
        assert isinstance(repeated.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)
        assert repeated.body["id"] == run_id
        assert started.body["workspace_id"] == workspace_id
        assert started.body["workspace_snapshot_id"] == snapshot_id
        assert started.body["workspace_content_checksum"] == snapshot.content_checksum

        persisted = await bindings.get(run_id)
        assert persisted is not None
        assert persisted.task_id == task_id
        assert persisted.workspace_id == workspace_id
        assert persisted.workspace_snapshot_id == snapshot_id
        assert persisted.content_checksum == snapshot.content_checksum

        restarted_bindings = SqliteRunWorkspaceBindingRepository(tmp_path / "workspaces.sqlite")
        after_restart = await restarted_bindings.get(run_id)
        assert after_restart == persisted

        loaded = await http.handle(HTTPRequest(method="GET", path=f"/api/v1/runs/{run_id}"))
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["workspace_snapshot_id"] == snapshot_id

        listed = await http.handle(HTTPRequest(method="GET", path="/api/v1/runs"))
        items = assert_page(listed.body, total=1)
        assert isinstance(items[0], dict)
        assert items[0]["workspace_id"] == workspace_id

    asyncio.run(scenario())


def test_retry_preserves_previous_exact_workspace_snapshot_by_default(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, kernel, _, bindings = _stack(tmp_path)
        task_id, workspace_id, snapshot_id = await _project_workspace_task(http)
        started = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="task-start"),
                body={"workspace_id": workspace_id, "workspace_snapshot_id": snapshot_id},
            )
        )
        assert isinstance(started.body, dict)
        first_run_id = started.body["id"]
        assert isinstance(first_run_id, str)
        await kernel.record_run_outcome(
            idempotency_key="run-failed",
            task_id=task_id,
            run_id=first_run_id,
            status=RunStatus.FAILED,
        )

        retried = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:retry",
                headers=api_headers(idempotency_key="task-retry"),
            )
        )
        assert retried.status == 200
        assert isinstance(retried.body, dict)
        retry_run_id = retried.body["id"]
        assert isinstance(retry_run_id, str)
        assert retry_run_id != first_run_id
        assert retried.body["workspace_id"] == workspace_id
        assert retried.body["workspace_snapshot_id"] == snapshot_id

        first_binding = await bindings.get(first_run_id)
        retry_binding = await bindings.get(retry_run_id)
        assert first_binding is not None and retry_binding is not None
        assert retry_binding.workspace_id == first_binding.workspace_id
        assert retry_binding.workspace_snapshot_id == first_binding.workspace_snapshot_id
        assert retry_binding.content_checksum == first_binding.content_checksum

    asyncio.run(scenario())


def test_workspace_bound_start_rejects_cross_project_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, _, _, _ = _stack(tmp_path)
        task_id, _, _ = await _project_workspace_task(http)

        other_project = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="other-project"),
                body={
                    "name": "Other",
                    "owner_type": "user",
                    "owner_id": "workspace-user",
                },
            )
        )
        assert isinstance(other_project.body, dict)
        other_project_id = other_project.body["id"]
        assert isinstance(other_project_id, str)
        other_workspace = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="other-workspace"),
                body={"project_id": other_project_id},
            )
        )
        assert isinstance(other_workspace.body, dict)
        other_workspace_id = other_workspace.body["id"]
        assert isinstance(other_workspace_id, str)

        rejected = await http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="cross-project-start"),
                body={"workspace_id": other_workspace_id},
            )
        )
        assert rejected.status == 409
        assert isinstance(rejected.body, dict)
        assert rejected.body["code"] == "conflict"

    asyncio.run(scenario())
