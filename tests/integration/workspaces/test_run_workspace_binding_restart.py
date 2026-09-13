from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_contract_helpers import api_headers

from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import LocalFileProvider
from ai_multi_agent_platform.domain import RunStatus
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    SqliteRunWorkspaceBindingRepository,
    SqliteWorkspaceProvider,
)


def _persistent_stack(
    tmp_path: Path,
) -> tuple[
    ControlPlaneHTTP,
    PlatformKernel,
    SqliteWorkspaceProvider,
    SqliteRunWorkspaceBindingRepository,
]:
    repository = SqliteKernelRepository(tmp_path / "kernel.sqlite")
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


async def _create_project_workspace_task(
    http: ControlPlaneHTTP,
) -> tuple[str, str, str]:
    project = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=api_headers(idempotency_key="restart-project"),
            body={
                "name": "Restart workspace project",
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
            headers=api_headers(idempotency_key="restart-workspace"),
            body={"project_id": project_id, "workspace_type": "isolated_run"},
        )
    )
    assert workspace.status == 201
    assert isinstance(workspace.body, dict)
    workspace_id = workspace.body["id"]
    snapshot_id = workspace.body["base_snapshot_id"]
    assert isinstance(workspace_id, str)
    assert isinstance(snapshot_id, str)

    task = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/tasks",
            headers=api_headers(idempotency_key="restart-task"),
            body={
                "title": "Restart-bound task",
                "objective": "Recover exact workspace input after process restart",
                "owner_type": "user",
                "owner_id": "contract-test",
                "project_id": project_id,
            },
        )
    )
    assert task.status == 201
    assert isinstance(task.body, dict)
    task_id = task.body["id"]
    assert isinstance(task_id, str)

    queued = await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/tasks/{task_id}:queue",
            headers=api_headers(idempotency_key="restart-queue"),
        )
    )
    assert queued.status == 200
    return task_id, workspace_id, snapshot_id


def test_restart_between_run_creation_and_binding_recovers_same_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, kernel, provider, bindings = _persistent_stack(tmp_path)
        task_id, workspace_id, snapshot_id = await _create_project_workspace_task(http)
        snapshot = await provider.get_snapshot(snapshot_id)

        task = await kernel.get_task(task_id)
        assert task.plan_ref is None
        await kernel.plan_task(
            idempotency_key="restart-start:plan",
            task_id=task_id,
            actor_ref="user:contract-test",
            source="control-plane",
        )
        queued_run = await kernel.create_run(
            idempotency_key="restart-start:create-run",
            task_id=task_id,
            actor_ref="user:contract-test",
            source="control-plane",
        )
        assert queued_run.status is RunStatus.QUEUED
        assert await bindings.get(queued_run.run_id) is None

        restarted_http, restarted_kernel, _, restarted_bindings = _persistent_stack(tmp_path)
        resumed = await restarted_http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=api_headers(idempotency_key="restart-start"),
                body={
                    "workspace_id": workspace_id,
                    "workspace_snapshot_id": snapshot_id,
                },
            )
        )
        assert resumed.status == 200
        assert isinstance(resumed.body, dict)
        assert resumed.body["id"] == queued_run.run_id
        assert resumed.body["workspace_id"] == workspace_id
        assert resumed.body["workspace_snapshot_id"] == snapshot_id
        assert resumed.body["workspace_content_checksum"] == snapshot.content_checksum

        binding = await restarted_bindings.get(queued_run.run_id)
        assert binding is not None
        assert binding.workspace_id == workspace_id
        assert binding.workspace_snapshot_id == snapshot_id
        assert binding.content_checksum == snapshot.content_checksum

        canonical_run = await restarted_kernel.get_run(task_id, queued_run.run_id)
        assert canonical_run.status is RunStatus.RUNNING

    asyncio.run(scenario())
