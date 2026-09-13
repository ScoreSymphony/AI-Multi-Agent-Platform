from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.execution import ExecutionRequest, ExecutionStatus, ReferenceExecutor
from ai_multi_agent_platform.workspaces import (
    LocalWorkspaceProvider,
    MaterializationOutcome,
    WorkspaceAccessMode,
    WorkspaceChangeKind,
    WorkspaceFile,
    WorkspaceRetention,
    WorkspaceType,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="workspace-tests",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


async def _providers(
    tmp_path: Path,
) -> tuple[LocalFileProvider, LocalWorkspaceProvider, DataAccessContext, WorkspaceFile]:
    project_id = new_id("project")
    context = _context(project_id)
    files = LocalFileProvider(tmp_path / "objects", tmp_path / "data.sqlite")
    record = await files.create_file(b"hello\n", context, content_type="text/plain")
    workspace_file = WorkspaceFile(
        relative_path="src/input.txt",
        file_id=record.file_id,
        sha256=record.sha256,
    )
    workspaces = LocalWorkspaceProvider(tmp_path / "workspaces", files)
    return files, workspaces, context, workspace_file


def test_create_persistent_and_ephemeral_workspaces(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        owner = OwnerRef(type="user", id="workspace-user")
        persistent = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=owner,
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        ephemeral = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=owner,
            workspace_type=WorkspaceType.EPHEMERAL_TASK,
            context=context,
            retention=WorkspaceRetention.EPHEMERAL,
        )

        assert persistent.id.startswith("workspace_")
        assert persistent.base_snapshot_id is not None
        assert persistent.workspace_type is WorkspaceType.PERSISTENT_PROJECT
        assert ephemeral.workspace_type is WorkspaceType.EPHEMERAL_TASK
        assert ephemeral.retention is WorkspaceRetention.EPHEMERAL
        assert persistent.id != ephemeral.id

    asyncio.run(scenario())


def test_local_executor_uses_bounded_materialization_and_returns_canonical_changes(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        files, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        materialization = await provider.materialize(workspace.id, context)
        executor = ReferenceExecutor(provider.materialization_root)
        result = await executor.execute(
            ExecutionRequest(
                task_id="task-local",
                run_id="run-local",
                correlation_id="workspace-executor",
                action="write_artifact",
                workspace=materialization.execution_workspace,
                arguments={"path": "out/result.txt", "content": "ok"},
            )
        )
        assert result.status is ExecutionStatus.SUCCEEDED

        changes = await provider.capture_changes(materialization.id, context)
        returned = next(
            change for change in changes.changes if change.relative_path == "out/result.txt"
        )
        assert returned.kind is WorkspaceChangeKind.CREATED
        assert returned.file_id is not None
        assert await files.read(returned.file_id, context.operation) == b"ok"
        assert materialization.execution_workspace == materialization.id
        assert str(provider.local_path(materialization.id)).startswith(
            str(provider.materialization_root)
        )

    asyncio.run(scenario())


def test_repeat_materialization_of_same_snapshot_is_content_stable(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        first = await provider.materialize(workspace.id, context)
        second = await provider.materialize(workspace.id, context, snapshot_id=first.snapshot_id)

        assert first.id != second.id
        assert first.snapshot_id == second.snapshot_id
        assert (provider.local_path(first.id) / "src/input.txt").read_bytes() == b"hello\n"
        assert (provider.local_path(second.id) / "src/input.txt").read_bytes() == b"hello\n"

    asyncio.run(scenario())


def test_read_only_materialization_rejects_changes_even_if_permissions_are_bypassed(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.READ_ONLY_SOURCE,
            access_mode=WorkspaceAccessMode.READ_ONLY,
            context=context,
            files=(source,),
        )
        materialization = await provider.materialize(workspace.id, context)
        root = provider.local_path(materialization.id)
        target = root / "src/input.txt"
        root.chmod(0o700)
        target.parent.chmod(0o700)
        target.chmod(0o600)
        target.write_text("tampered", encoding="utf-8")

        with pytest.raises(ContractError):
            await provider.capture_changes(materialization.id, context)

        await provider.release_materialization(
            materialization.id,
            MaterializationOutcome.CANCELLED,
        )

    asyncio.run(scenario())


def test_workspace_relative_path_traversal_is_rejected() -> None:
    fake_file = new_id("file")
    checksum = "0" * 64
    with pytest.raises(ValueError):
        WorkspaceFile(relative_path="../escape.txt", file_id=fake_file, sha256=checksum)
    with pytest.raises(ValueError):
        WorkspaceFile(relative_path="/absolute.txt", file_id=fake_file, sha256=checksum)
    with pytest.raises(ValueError):
        WorkspaceFile(relative_path="a\\b.txt", file_id=fake_file, sha256=checksum)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        materialization = await provider.materialize(workspace.id, context)
        outside = tmp_path / "outside"
        outside.mkdir()
        link = provider.local_path(materialization.id) / "linked-outside"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is not supported by this environment")

        with pytest.raises(ContractError):
            await provider.capture_changes(materialization.id, context)

    asyncio.run(scenario())


def test_snapshot_checksum_is_stable_for_unchanged_content(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        assert workspace.base_snapshot_id is not None
        first = await provider.get_snapshot(workspace.base_snapshot_id)
        second = await provider.create_snapshot(workspace.id)

        assert first.id != second.id
        assert first.content_checksum == second.content_checksum
        assert first.files == second.files
        assert second.parent_snapshot_id == first.id

    asyncio.run(scenario())


def test_stale_concurrent_commit_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
            files=(source,),
        )
        first = await provider.materialize(workspace.id, context)
        second = await provider.materialize(workspace.id, context)
        (provider.local_path(first.id) / "src/input.txt").write_text("first", encoding="utf-8")
        (provider.local_path(second.id) / "src/input.txt").write_text("second", encoding="utf-8")

        committed = await provider.commit_changes(first.id, context, expected_revision=0)
        assert committed.revision == 1
        with pytest.raises(ContractError):
            await provider.commit_changes(second.id, context, expected_revision=0)

    asyncio.run(scenario())


def test_cancelled_materialization_is_cleaned(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, source = await _providers(tmp_path)
        workspace = await provider.create_workspace(
            project_id=context.project_id or "",
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.ISOLATED_RUN,
            context=context,
            retention=WorkspaceRetention.EPHEMERAL,
            files=(source,),
        )
        materialization = await provider.materialize(workspace.id, context)
        path = provider.local_path(materialization.id)
        assert path.exists()

        await provider.release_materialization(
            materialization.id,
            MaterializationOutcome.CANCELLED,
        )
        assert not path.exists()
        with pytest.raises(ContractError):
            provider.local_path(materialization.id)

    asyncio.run(scenario())


def test_missing_canonical_file_reference_is_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, context, _ = await _providers(tmp_path)
        missing = WorkspaceFile(
            relative_path="missing.txt",
            file_id=new_id("file"),
            sha256="0" * 64,
        )
        with pytest.raises(ContractError):
            await provider.create_workspace(
                project_id=context.project_id or "",
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=context,
                files=(missing,),
            )

    asyncio.run(scenario())


def test_cleanup_removes_orphaned_local_materialization(tmp_path: Path) -> None:
    async def scenario() -> None:
        _, provider, _, _ = await _providers(tmp_path)
        orphan_id = new_id("materialization")
        orphan = provider.materialization_root / orphan_id
        orphan.mkdir()
        (orphan / "temp.txt").write_text("orphan", encoding="utf-8")

        report = await provider.cleanup()
        assert orphan_id in report.removed_materialization_ids
        assert not orphan.exists()

    asyncio.run(scenario())
