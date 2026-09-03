from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    MaterializationOutcome,
    RetentionManagedWorkspaceProvider,
    SqliteWorkspaceProvider,
    WorkspaceRetention,
    WorkspaceRetentionGuard,
    WorkspaceType,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="retention-tests",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


class DenyCleanup(WorkspaceRetentionGuard):
    async def allow_cleanup(self, workspace: object) -> bool:
        del workspace
        return False


def test_ephemeral_workspace_is_deleted_only_after_use_and_preserves_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        provider = RetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "materializations", files)
        )
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.EPHEMERAL_TASK,
            retention=WorkspaceRetention.EPHEMERAL,
            context=context,
        )
        snapshot_id = workspace.base_snapshot_id
        assert snapshot_id is not None

        unused = await provider.enforce_retention()
        assert workspace.id in unused.retained_workspace_ids

        materialization = await provider.materialize(workspace.id, context)
        active = await provider.enforce_retention()
        assert workspace.id in active.deferred_workspace_ids
        await provider.release_materialization(
            materialization.id,
            MaterializationOutcome.SUCCEEDED,
        )

        expired = await provider.enforce_retention()
        assert expired.deleted_workspace_ids == (workspace.id,)
        with pytest.raises(ContractError) as error:
            await provider.get_workspace(workspace.id)
        assert error.value.code is ErrorCode.NOT_FOUND
        assert (await provider.get_snapshot(snapshot_id)).id == snapshot_id

    asyncio.run(scenario())


def test_until_retention_expires_deterministically_and_guard_can_defer(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        provider = RetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "materializations", files)
        )
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
        )
        now = datetime.now(UTC)
        configured = await provider.set_retention(
            workspace.id,
            WorkspaceRetention.UNTIL,
            expires_at=now - timedelta(seconds=1),
        )
        assert configured.retention is WorkspaceRetention.UNTIL

        deferred = await provider.enforce_retention(now=now, guard=DenyCleanup())
        assert deferred.deferred_workspace_ids == (workspace.id,)
        assert (await provider.get_workspace(workspace.id)).id == workspace.id

        expired = await provider.enforce_retention(now=now)
        assert expired.deleted_workspace_ids == (workspace.id,)

    asyncio.run(scenario())


def test_persistent_workspace_is_retained(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        provider = RetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "materializations", files)
        )
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
        )
        report = await provider.enforce_retention()
        assert report.retained_workspace_ids == (workspace.id,)
        assert (await provider.get_workspace(workspace.id)).id == workspace.id

    asyncio.run(scenario())


def test_retention_tombstone_survives_restart_without_deleting_canonical_snapshot(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        workspace_db = tmp_path / "workspaces.sqlite"
        retention_db = tmp_path / "retention.sqlite"
        provider = RetentionManagedWorkspaceProvider(
            SqliteWorkspaceProvider(tmp_path / "materializations", files, workspace_db),
            metadata_db_path=retention_db,
        )
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.EPHEMERAL_TASK,
            retention=WorkspaceRetention.EPHEMERAL,
            context=context,
        )
        snapshot_id = workspace.base_snapshot_id
        assert snapshot_id is not None
        materialization = await provider.materialize(workspace.id, context)
        await provider.release_materialization(
            materialization.id,
            MaterializationOutcome.CANCELLED,
        )
        assert (await provider.enforce_retention()).deleted_workspace_ids == (workspace.id,)

        restarted = RetentionManagedWorkspaceProvider(
            SqliteWorkspaceProvider(tmp_path / "materializations", files, workspace_db),
            metadata_db_path=retention_db,
        )
        with pytest.raises(ContractError) as error:
            await restarted.get_workspace(workspace.id)
        assert error.value.code is ErrorCode.NOT_FOUND
        assert (await restarted.get_snapshot(snapshot_id)).id == snapshot_id

    asyncio.run(scenario())
