from __future__ import annotations

import asyncio
from pathlib import Path

from control_plane_contract_helpers import api_headers, assert_page

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import (
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    build_openapi,
)
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider


def _stack(tmp_path: Path) -> tuple[ControlPlaneHTTP, LocalFileProvider, SqliteWorkspaceProvider]:
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
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        workspace_provider=workspaces,
    )
    return ControlPlaneHTTP(control_plane), files, workspaces


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="control-plane-workspaces",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


def test_control_plane_uses_canonical_workspace_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, files, provider = _stack(tmp_path)
        project_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="project-create"),
                body={
                    "name": "Workspace project",
                    "owner_type": "user",
                    "owner_id": "workspace-user",
                },
            )
        )
        assert project_response.status == 201
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        file_record = await files.create_file(
            b"canonical input\n",
            _data_context(project_id),
            content_type="text/plain",
        )
        workspace_request = HTTPRequest(
            method="POST",
            path="/api/v1/workspaces",
            headers=api_headers(idempotency_key="workspace-create"),
            body={
                "project_id": project_id,
                "workspace_type": "isolated_run",
                "source_refs": [{"kind": "files", "ref": "canonical-upload"}],
                "files": [
                    {
                        "relative_path": "src/input.txt",
                        "file_id": file_record.file_id,
                        "sha256": file_record.sha256,
                    }
                ],
            },
        )
        created = await http.handle(workspace_request)
        duplicate = await http.handle(workspace_request)
        assert created.status == duplicate.status == 201
        assert isinstance(created.body, dict)
        assert isinstance(duplicate.body, dict)
        assert duplicate.body["id"] == created.body["id"]
        assert created.body["lifecycle"] == "canonical"
        assert created.body["workspace_type"] == "isolated_run"
        assert created.body["retention"] == "ephemeral"
        assert created.body["revision"] == 0
        snapshot_id = created.body["base_snapshot_id"]
        assert isinstance(snapshot_id, str) and snapshot_id.startswith("workspace_snapshot_")

        workspace_id = created.body["id"]
        assert isinstance(workspace_id, str)
        loaded = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/workspaces/{workspace_id}")
        )
        assert loaded.status == 200
        assert loaded.body == created.body

        listed = await http.handle(HTTPRequest(method="GET", path="/api/v1/workspaces"))
        items = assert_page(listed.body, total=1)
        assert isinstance(items[0], dict)
        assert items[0]["id"] == workspace_id
        assert items[0]["base_snapshot_id"] == snapshot_id

        snapshot = await provider.get_snapshot(snapshot_id)
        assert snapshot.workspace_id == workspace_id
        assert len(snapshot.files) == 1
        assert snapshot.files[0].file_id == file_record.file_id
        assert snapshot.files[0].relative_path == "src/input.txt"

    asyncio.run(scenario())


def test_control_plane_workspace_validation_is_canonical(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, _, _ = _stack(tmp_path)
        project_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=api_headers(idempotency_key="project-create"),
                body={
                    "name": "Workspace project",
                    "owner_type": "user",
                    "owner_id": "workspace-user",
                },
            )
        )
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        invalid = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="workspace-invalid"),
                body={"project_id": project_id, "workspace_type": "host-path-workspace"},
            )
        )
        assert invalid.status == 400
        assert isinstance(invalid.body, dict)
        assert invalid.body["code"] == "invalid_request"

    asyncio.run(scenario())


def test_workspace_openapi_exposes_canonical_lifecycle_fields() -> None:
    specification = build_openapi()
    workspace = specification["components"]["schemas"]["Workspace"]
    properties = workspace["properties"]
    assert "workspace_type" in properties
    assert "base_snapshot_id" in properties
    assert "revision" in properties
    assert "retention" in properties
