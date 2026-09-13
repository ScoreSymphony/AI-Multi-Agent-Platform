from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    EmptyWorkspaceSourceResolver,
    LocalWorkspaceProvider,
    SnapshotWorkspaceSourceResolver,
    WorkspaceFile,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceSourceResolverRegistry,
    WorkspaceType,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="workspace-source-tests",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


def test_empty_and_snapshot_sources_resolve_to_canonical_file_references(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        record = await files.create_file(b"source\n", context, content_type="text/plain")
        workspaces = LocalWorkspaceProvider(tmp_path / "materializations", files)
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
        assert workspace.base_snapshot_id is not None
        snapshot = await workspaces.get_snapshot(workspace.base_snapshot_id)
        registry = WorkspaceSourceResolverRegistry(
            (
                EmptyWorkspaceSourceResolver(),
                SnapshotWorkspaceSourceResolver(workspaces),
            )
        )

        empty = await registry.resolve(
            WorkspaceSourceRef(kind=WorkspaceSourceKind.EMPTY, ref="empty"),
            context,
        )
        resolved = await registry.resolve(
            WorkspaceSourceRef(
                kind=WorkspaceSourceKind.SNAPSHOT,
                ref=snapshot.id,
                revision=str(snapshot.revision),
                checksum=snapshot.content_checksum,
            ),
            context,
        )

        assert empty.files == ()
        assert resolved.files == snapshot.files
        assert resolved.files[0].file_id == record.file_id

    asyncio.run(scenario())


def test_unregistered_connector_source_is_explicitly_unavailable(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    registry = WorkspaceSourceResolverRegistry((EmptyWorkspaceSourceResolver(),))

    async def scenario() -> None:
        with pytest.raises(ContractError) as error:
            await registry.resolve(
                WorkspaceSourceRef(
                    kind=WorkspaceSourceKind.REPOSITORY,
                    ref="repo:example/project",
                ),
                context,
            )
        assert error.value.code is ErrorCode.UNAVAILABLE
        assert error.value.details == {"source_kind": "repository"}

    asyncio.run(scenario())


def test_snapshot_source_rejects_wrong_integrity_evidence(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        workspaces = LocalWorkspaceProvider(tmp_path / "materializations", files)
        workspace = await workspaces.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=context,
        )
        assert workspace.base_snapshot_id is not None
        resolver = SnapshotWorkspaceSourceResolver(workspaces)
        with pytest.raises(ContractError) as error:
            await resolver.resolve(
                WorkspaceSourceRef(
                    kind=WorkspaceSourceKind.SNAPSHOT,
                    ref=workspace.base_snapshot_id,
                    checksum="f" * 64,
                ),
                context,
            )
        assert error.value.code is ErrorCode.CONTRACT_VIOLATION

    asyncio.run(scenario())
