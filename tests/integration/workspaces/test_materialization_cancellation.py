from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import LocalWorkspaceProvider, WorkspaceFile, WorkspaceType


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="workspace-materialization-cancellation",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


class _BlockingWorkspaceProvider(LocalWorkspaceProvider):
    def __init__(self, root: Path, files: LocalFileProvider) -> None:
        super().__init__(root, files)
        self.read_started = asyncio.Event()

    async def _read_file(self, file_id: str, context: DataAccessContext) -> bytes:
        del file_id, context
        self.read_started.set()
        await asyncio.Event().wait()
        raise AssertionError("blocked workspace read unexpectedly resumed")


def test_materialize_removes_partial_local_workspace_before_cancellation_propagates(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        record = await files.create_file(b"source\n", context, content_type="text/plain")
        workspaces = _BlockingWorkspaceProvider(tmp_path / "materializations", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(
                WorkspaceFile(
                    relative_path="src/source.txt",
                    file_id=record.file_id,
                    sha256=record.sha256,
                ),
            ),
        )

        materializing = asyncio.create_task(workspaces.materialize(workspace.id, context))
        await workspaces.read_started.wait()
        assert tuple(workspaces.materialization_root.iterdir())

        materializing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await materializing

        assert tuple(workspaces.materialization_root.iterdir()) == ()

    asyncio.run(scenario())
