from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from control_plane_contract_helpers import api_headers

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    SqliteWorkspaceProvider,
    WorkspaceFile,
    WorkspaceType,
)


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue37-source-lifecycle",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


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


async def _create_project(http: ControlPlaneHTTP, key: str, name: str) -> str:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path="/api/v1/projects",
            headers=api_headers(idempotency_key=key),
            body={
                "name": name,
                "owner_type": "user",
                "owner_id": "workspace-user",
            },
        )
    )
    assert response.status == 201
    assert isinstance(response.body, dict)
    project_id = response.body["id"]
    assert isinstance(project_id, str)
    return project_id


def test_snapshot_source_is_frozen_into_initial_workspace_snapshot_and_materializes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        http, files, provider = _stack(tmp_path)
        project_id = await _create_project(http, "source-project", "Source project")
        record = await files.create_file(
            b"source-v1\n",
            _data_context(project_id),
            content_type="text/plain",
        )

        source_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="source-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "persistent_project",
                    "source_refs": [{"kind": "files", "ref": "canonical-upload"}],
                    "files": [
                        {
                            "relative_path": "src/input.txt",
                            "file_id": record.file_id,
                            "sha256": record.sha256,
                        }
                    ],
                },
            )
        )
        assert source_response.status == 201
        assert isinstance(source_response.body, dict)
        source_workspace_id = source_response.body["id"]
        source_snapshot_id = source_response.body["base_snapshot_id"]
        assert isinstance(source_workspace_id, str)
        assert isinstance(source_snapshot_id, str)
        source_snapshot = await provider.get_snapshot(source_snapshot_id)

        derived_response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="derived-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "isolated_run",
                    "source_refs": [
                        {
                            "kind": "snapshot",
                            "ref": source_snapshot.id,
                            "revision": str(source_snapshot.revision),
                            "checksum": source_snapshot.content_checksum,
                        }
                    ],
                },
            )
        )
        assert derived_response.status == 201
        assert isinstance(derived_response.body, dict)
        derived_workspace_id = derived_response.body["id"]
        derived_snapshot_id = derived_response.body["base_snapshot_id"]
        assert isinstance(derived_workspace_id, str)
        assert isinstance(derived_snapshot_id, str)

        derived_snapshot = await provider.get_snapshot(derived_snapshot_id)
        assert derived_snapshot.workspace_id == derived_workspace_id
        assert derived_snapshot.files == source_snapshot.files
        assert derived_snapshot.source_refs[0].ref == source_snapshot.id

        source_materialization = await provider.materialize(
            source_workspace_id,
            _data_context(project_id),
        )
        source_path = provider.local_path(source_materialization.id) / "src/input.txt"
        source_path.write_bytes(b"source-v2\n")
        await provider.commit_changes(
            source_materialization.id,
            _data_context(project_id),
            expected_revision=0,
        )

        derived_materialization = await provider.materialize(
            derived_workspace_id,
            _data_context(project_id),
        )
        assert (
            provider.local_path(derived_materialization.id) / "src/input.txt"
        ).read_bytes() == b"source-v1\n"

    asyncio.run(scenario())


def test_snapshot_source_rejects_cross_project_attachment(tmp_path: Path) -> None:
    async def scenario() -> None:
        http, _, provider = _stack(tmp_path)
        source_project_id = await _create_project(http, "source-project", "Source project")
        target_project_id = await _create_project(http, "target-project", "Target project")

        source = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="source-workspace"),
                body={
                    "project_id": source_project_id,
                    "workspace_type": "persistent_project",
                },
            )
        )
        assert source.status == 201
        assert isinstance(source.body, dict)
        snapshot_id = source.body["base_snapshot_id"]
        assert isinstance(snapshot_id, str)
        snapshot = await provider.get_snapshot(snapshot_id)

        rejected = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="cross-project-workspace"),
                body={
                    "project_id": target_project_id,
                    "workspace_type": "isolated_run",
                    "source_refs": [
                        {
                            "kind": "snapshot",
                            "ref": snapshot.id,
                            "checksum": snapshot.content_checksum,
                        }
                    ],
                },
            )
        )
        assert rejected.status == 403
        assert isinstance(rejected.body, dict)
        assert rejected.body["code"] == "forbidden"

    asyncio.run(scenario())


def test_source_resolution_rejects_explicit_file_overlap_and_unregistered_connector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        http, files, provider = _stack(tmp_path)
        project_id = await _create_project(http, "source-project", "Source project")
        record = await files.create_file(
            b"source\n",
            _data_context(project_id),
            content_type="text/plain",
        )
        source = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=_data_context(project_id),
            files=(
                WorkspaceFile(
                    relative_path="src/input.txt",
                    file_id=record.file_id,
                    sha256=record.sha256,
                ),
            ),
        )
        assert source.base_snapshot_id is not None
        snapshot = await provider.get_snapshot(source.base_snapshot_id)

        overlap = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="overlap-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "isolated_run",
                    "source_refs": [{"kind": "snapshot", "ref": snapshot.id}],
                    "files": [
                        {
                            "relative_path": "src/input.txt",
                            "file_id": record.file_id,
                            "sha256": record.sha256,
                        }
                    ],
                },
            )
        )
        assert overlap.status == 409
        assert isinstance(overlap.body, dict)
        assert overlap.body["code"] == "conflict"

        unavailable = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=api_headers(idempotency_key="repository-workspace"),
                body={
                    "project_id": project_id,
                    "workspace_type": "isolated_run",
                    "source_refs": [{"kind": "repository", "ref": "repo:example/project"}],
                },
            )
        )
        assert unavailable.status == 503
        assert isinstance(unavailable.body, dict)
        assert unavailable.body["code"] == "unavailable"

    asyncio.run(scenario())


def test_cleanup_reconciles_missing_known_materialization_and_active_refs(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _data_context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        provider = LocalWorkspaceProvider(tmp_path / "materializations", files)
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=context,
        )
        task_id = new_id("task")
        run_id = new_id("run")
        materialization = await provider.materialize(
            workspace.id,
            context,
            task_id=task_id,
            run_id=run_id,
        )
        path = provider.local_path(materialization.id)
        assert path.exists()
        active = await provider.get_workspace(workspace.id)
        assert active.active_task_ids == (task_id,)
        assert active.active_run_ids == (run_id,)

        shutil.rmtree(path)
        report = await provider.cleanup()
        assert report.missing_materialization_ids == (materialization.id,)

        reconciled = await provider.get_workspace(workspace.id)
        assert reconciled.active_task_ids == ()
        assert reconciled.active_run_ids == ()
        with pytest.raises(ContractError):
            provider.local_path(materialization.id)

    asyncio.run(scenario())
