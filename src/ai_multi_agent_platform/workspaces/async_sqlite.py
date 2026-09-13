"""Event-loop-safe SQLite adapters for Workspace runtime persistence."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import OwnerRef, validate_id

from ._sqlite_async import AsyncSqliteOffload, map_sqlite_error
from .models import (
    MaterializationOutcome,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceRef,
    WorkspaceType,
)
from .run_bindings import RunWorkspaceBinding, _time
from .run_bindings import (
    SqliteRunWorkspaceBindingRepository as _SyncRunWorkspaceBindingRepository,
)
from .sqlite import SqliteWorkspaceProvider as _SyncWorkspaceProvider


class SqliteWorkspaceProvider(_SyncWorkspaceProvider):
    """Workspace metadata provider with non-blocking runtime SQLite checkpoints."""

    def __init__(
        self,
        root: str | Path,
        files: FileProvider,
        db_path: str | Path,
        *,
        max_concurrency: int = 1,
    ) -> None:
        super().__init__(root, files, db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def create_workspace(
        self,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        workspace_type: WorkspaceType,
        context: DataAccessContext,
        access_mode: WorkspaceAccessMode = WorkspaceAccessMode.READ_WRITE,
        retention: WorkspaceRetention = WorkspaceRetention.PERSISTENT,
        source_refs: tuple[WorkspaceSourceRef, ...] = (),
        files: tuple[WorkspaceFile, ...] = (),
        workspace_id: str | None = None,
    ) -> Workspace:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            workspace = await super(_SyncWorkspaceProvider, self).create_workspace(
                project_id=project_id,
                owner_ref=owner_ref,
                workspace_type=workspace_type,
                context=context,
                access_mode=access_mode,
                retention=retention,
                source_refs=source_refs,
                files=files,
                workspace_id=workspace_id,
            )
            await self._persist_or_restore_async(checkpoint)
            return workspace

    async def create_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            snapshot = await super(_SyncWorkspaceProvider, self).create_snapshot(workspace_id)
            await self._persist_or_restore_async(checkpoint)
            return snapshot

    async def materialize(
        self,
        workspace_id: str,
        context: DataAccessContext,
        *,
        snapshot_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> WorkspaceMaterialization:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            materialization = await super(_SyncWorkspaceProvider, self).materialize(
                workspace_id,
                context,
                snapshot_id=snapshot_id,
                task_id=task_id,
                run_id=run_id,
            )
            await self._persist_or_restore_async(checkpoint)
            return materialization

    async def commit_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
        *,
        expected_revision: int,
    ) -> WorkspaceSnapshot:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            snapshot = await super(_SyncWorkspaceProvider, self).commit_changes(
                materialization_id,
                context,
                expected_revision=expected_revision,
            )
            await self._persist_or_restore_async(checkpoint)
            return snapshot

    async def release_materialization(
        self,
        materialization_id: str,
        outcome: MaterializationOutcome,
    ) -> None:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            await super(_SyncWorkspaceProvider, self).release_materialization(
                materialization_id,
                outcome,
            )
            await self._persist_or_restore_async(checkpoint)

    async def _persist_or_restore_async(
        self,
        checkpoint: tuple[dict[str, Workspace], dict[str, WorkspaceSnapshot], dict[str, str]],
    ) -> None:
        try:
            await self._async_sqlite.run(self._persist_state, write=True)
        except ContractError as exc:
            self._workspaces, self._snapshots, self._heads = checkpoint
            cause = exc.__cause__
            if isinstance(cause, sqlite3.Error):
                raise map_sqlite_error(cause, "failed to persist workspace metadata") from cause
            raise


class SqliteRunWorkspaceBindingRepository(_SyncRunWorkspaceBindingRepository):
    """Run/workspace binding repository whose async methods offload SQLite work."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path)
        self._async_sqlite = AsyncSqliteOffload(max_concurrency=max_concurrency)

    async def _run_sqlite[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._async_sqlite.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise map_sqlite_error(exc, message) from exc

    def _get_sync(self, run_id: str) -> RunWorkspaceBinding | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM run_workspace_bindings WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return RunWorkspaceBinding(
                run_id=cast(str, row["run_id"]),
                task_id=cast(str, row["task_id"]),
                workspace_id=cast(str, row["workspace_id"]),
                workspace_snapshot_id=cast(str, row["workspace_snapshot_id"]),
                content_checksum=cast(str, row["content_checksum"]),
                created_at=_time(cast(str, row["created_at"])),
            )
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored run workspace binding is invalid",
            ) from exc

    async def get(self, run_id: str) -> RunWorkspaceBinding | None:
        validate_id(run_id, "run")
        return await self._run_sqlite(
            lambda: self._get_sync(run_id),
            message="failed to read run workspace binding",
        )

    async def bind(self, binding: RunWorkspaceBinding) -> RunWorkspaceBinding:
        def operation() -> RunWorkspaceBinding:
            existing = self._get_sync(binding.run_id)
            if existing is not None:
                if not existing.same_target(binding):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"run already has a different workspace binding: {binding.run_id}",
                    )
                return existing
            try:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT INTO run_workspace_bindings (
                            run_id, task_id, workspace_id, workspace_snapshot_id,
                            content_checksum, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            binding.run_id,
                            binding.task_id,
                            binding.workspace_id,
                            binding.workspace_snapshot_id,
                            binding.content_checksum,
                            binding.created_at.isoformat(),
                        ),
                    )
            except sqlite3.IntegrityError as exc:
                raced = self._get_sync(binding.run_id)
                if raced is not None and raced.same_target(binding):
                    return raced
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run already has a different workspace binding: {binding.run_id}",
                ) from exc
            return binding

        return await self._run_sqlite(
            operation,
            message="failed to persist run workspace binding",
            write=True,
        )


__all__ = ["SqliteRunWorkspaceBindingRepository", "SqliteWorkspaceProvider"]
