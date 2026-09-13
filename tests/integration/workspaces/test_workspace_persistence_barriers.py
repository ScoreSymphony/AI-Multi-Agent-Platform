from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import SqliteWorkspaceProvider, WorkspaceType


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-892-workspace-barrier",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


class _FailingSlowWorkspaceProvider(SqliteWorkspaceProvider):
    def __init__(self, root: Path, files: LocalFileProvider, db_path: Path) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.fail_runtime = False
        super().__init__(root, files, db_path)
        self.fail_runtime = True

    def _persist_state(self) -> None:
        if not self.fail_runtime:
            super()._persist_state()
            return
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("workspace persistence release timed out")
        raise ContractError(ErrorCode.BACKEND_ERROR, "synthetic workspace persistence failure")


def _provider(tmp_path: Path) -> _FailingSlowWorkspaceProvider:
    return _FailingSlowWorkspaceProvider(
        tmp_path / "materializations",
        LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3"),
        tmp_path / "workspaces.sqlite3",
    )


def test_uncommitted_workspace_state_is_hidden_until_failed_checkpoint_rolls_back(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider = _provider(tmp_path)
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        pending = asyncio.create_task(
            provider.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=_context(project_id),
                workspace_id=workspace_id,
            )
        )
        assert await asyncio.to_thread(provider.started.wait, 1)

        reader = asyncio.create_task(provider.get_workspace(workspace_id))
        listing = asyncio.create_task(provider.list_workspaces(project_id=project_id))
        await asyncio.sleep(0.02)
        assert not reader.done()
        assert not listing.done()

        provider.release.set()
        with pytest.raises(ContractError) as failed_write:
            await pending
        assert failed_write.value.code is ErrorCode.BACKEND_ERROR

        with pytest.raises(ContractError) as missing:
            await reader
        assert missing.value.code is ErrorCode.NOT_FOUND
        assert await listing == ()

    asyncio.run(scenario())


def test_cancelled_workspace_write_preserves_worker_failure_and_rolls_back(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        provider = _provider(tmp_path)
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        pending = asyncio.create_task(
            provider.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=_context(project_id),
                workspace_id=workspace_id,
            )
        )
        assert await asyncio.to_thread(provider.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        provider.release.set()

        with pytest.raises(ContractError) as failed_write:
            await pending
        assert failed_write.value.code is ErrorCode.BACKEND_ERROR

        with pytest.raises(ContractError) as missing:
            await provider.get_workspace(workspace_id)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())
