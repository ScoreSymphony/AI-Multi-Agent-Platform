from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import ai_multi_agent_platform.workspaces.sqlite as workspace_sqlite
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    SqliteWorkspaceProvider,
    WorkspaceFile,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="workspace-persistence-tests",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


async def _seed(tmp_path: Path) -> tuple[LocalFileProvider, DataAccessContext, WorkspaceFile]:
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
    record = await files.create_file(b"hello\n", context, content_type="text/plain")
    return (
        files,
        context,
        WorkspaceFile(
            relative_path="src/input.txt",
            file_id=record.file_id,
            sha256=record.sha256,
        ),
    )


def test_workspace_and_exact_snapshot_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        files, context, source = await _seed(tmp_path)
        provider = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            source_refs=(WorkspaceSourceRef(kind=WorkspaceSourceKind.FILES, ref="seed"),),
            files=(source,),
        )
        assert workspace.base_snapshot_id is not None
        original_snapshot = await provider.get_snapshot(workspace.base_snapshot_id)

        restarted = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        loaded = await restarted.get_workspace(workspace.id)
        loaded_snapshot = await restarted.get_snapshot(original_snapshot.id)

        assert loaded.id == workspace.id
        assert loaded.base_snapshot_id == original_snapshot.id
        assert loaded.revision == workspace.revision
        assert loaded_snapshot == original_snapshot

        materialization = await restarted.materialize(
            loaded.id,
            context,
            snapshot_id=original_snapshot.id,
        )
        assert (
            restarted.local_path(materialization.id) / "src/input.txt"
        ).read_bytes() == b"hello\n"
        await restarted.release_materialization(
            materialization.id,
            MaterializationOutcome.SUCCEEDED,
        )

    asyncio.run(scenario())


def test_committed_revision_and_manifest_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        files, context, source = await _seed(tmp_path)
        provider = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        original_snapshot_id = workspace.base_snapshot_id
        assert original_snapshot_id is not None
        materialization = await provider.materialize(workspace.id, context)
        root = provider.local_path(materialization.id)
        (root / "src/input.txt").write_bytes(b"changed\n")
        (root / "out").mkdir()
        (root / "out/result.txt").write_text("result", encoding="utf-8")
        committed = await provider.commit_changes(
            materialization.id,
            context,
            expected_revision=0,
        )
        await provider.release_materialization(
            materialization.id,
            MaterializationOutcome.SUCCEEDED,
        )

        restarted = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        loaded = await restarted.get_workspace(workspace.id)
        loaded_snapshot = await restarted.get_snapshot(committed.id)
        assert loaded.revision == 1
        assert loaded.base_snapshot_id == committed.id
        assert loaded_snapshot.parent_snapshot_id == original_snapshot_id
        assert {entry.relative_path for entry in loaded_snapshot.files} == {
            "src/input.txt",
            "out/result.txt",
        }

        rematerialized = await restarted.materialize(
            workspace.id,
            context,
            snapshot_id=committed.id,
        )
        restarted_root = restarted.local_path(rematerialized.id)
        assert (restarted_root / "src/input.txt").read_bytes() == b"changed\n"
        assert (restarted_root / "out/result.txt").read_text(encoding="utf-8") == "result"
        await restarted.release_materialization(
            rematerialized.id,
            MaterializationOutcome.SUCCEEDED,
        )

    asyncio.run(scenario())


def test_restart_clears_stale_active_refs_and_cleanup_finds_crash_orphan(tmp_path: Path) -> None:
    async def scenario() -> None:
        files, context, source = await _seed(tmp_path)
        provider = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=context,
            files=(source,),
        )
        task_id = new_id("task")
        run_id = new_id("run")
        materialization = await provider.materialize(
            workspace.id,
            context,
            task_id=task_id,
            run_id=run_id,
        )
        active = await provider.get_workspace(workspace.id)
        assert active.active_task_ids == (task_id,)
        assert active.active_run_ids == (run_id,)
        assert provider.local_path(materialization.id).exists()
        unowned = provider.materialization_root / "foreign_unowned"
        unowned.mkdir()

        restarted = SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )
        recovered = await restarted.get_workspace(workspace.id)
        assert recovered.active_task_ids == ()
        assert recovered.active_run_ids == ()
        assert not (restarted.materialization_root / materialization.id).exists()
        assert unowned.is_dir()

        report = await restarted.cleanup()
        assert report.removed_materialization_ids == ()
        assert unowned.is_dir()

    asyncio.run(scenario())


def test_restart_fails_closed_when_owned_workspace_cleanup_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def seed() -> tuple[LocalFileProvider, Path]:
        files, context, source = await _seed(tmp_path)
        root = tmp_path / "materializations"
        database = tmp_path / "workspaces.sqlite"
        provider = SqliteWorkspaceProvider(root, files, database)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=context,
            files=(source,),
        )
        materialization = await provider.materialize(workspace.id, context)
        return files, provider.local_path(materialization.id)

    files, materialization_path = asyncio.run(seed())
    assert materialization_path.exists()

    original_rmtree = workspace_sqlite.shutil.rmtree

    def fail_owned_cleanup(path: Path, *args, **kwargs) -> None:
        if Path(path) == materialization_path:
            raise OSError("injected cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(workspace_sqlite.shutil, "rmtree", fail_owned_cleanup)

    with pytest.raises(ContractError) as raised:
        SqliteWorkspaceProvider(
            tmp_path / "materializations",
            files,
            tmp_path / "workspaces.sqlite",
        )

    assert raised.value.code is ErrorCode.BACKEND_ERROR
    assert materialization_path.exists()
