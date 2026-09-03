"""Deterministic workspace retention policy layered over any canonical provider."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef, validate_id

from .contracts import WorkspaceProvider
from .models import (
    CleanupReport,
    MaterializationOutcome,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceChangeSet,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceRef,
    WorkspaceStatus,
    WorkspaceType,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class WorkspaceRetentionReport:
    deleted_workspace_ids: tuple[str, ...] = ()
    retained_workspace_ids: tuple[str, ...] = ()
    deferred_workspace_ids: tuple[str, ...] = ()
    failed_workspace_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for workspace_id in (
            *self.deleted_workspace_ids,
            *self.retained_workspace_ids,
            *self.deferred_workspace_ids,
            *self.failed_workspace_ids,
        ):
            validate_id(workspace_id, "workspace")


@runtime_checkable
class WorkspaceRetentionGuard(Protocol):
    """Policy/quota hook that may defer deletion of otherwise eligible workspaces."""

    async def allow_cleanup(self, workspace: Workspace) -> bool: ...


@runtime_checkable
class WorkspaceRetentionController(Protocol):
    async def set_retention(
        self,
        workspace_id: str,
        retention: WorkspaceRetention,
        *,
        expires_at: datetime | None = None,
    ) -> Workspace: ...

    async def enforce_retention(
        self,
        *,
        now: datetime | None = None,
        guard: WorkspaceRetentionGuard | None = None,
    ) -> WorkspaceRetentionReport: ...


@dataclass(frozen=True, slots=True)
class _RetentionState:
    retention: WorkspaceRetention
    expires_at: datetime | None
    ever_materialized: bool = False
    active_materializations: int = 0
    deleted: bool = False

    def __post_init__(self) -> None:
        if self.active_materializations < 0:
            raise ValueError("active_materializations must be non-negative")


class RetentionManagedWorkspaceProvider(WorkspaceProvider):
    """Add durable retention semantics without deleting canonical snapshots or file objects."""

    def __init__(
        self,
        delegate: WorkspaceProvider,
        *,
        metadata_db_path: str | Path | None = None,
    ) -> None:
        self._delegate = delegate
        self._lock = asyncio.Lock()
        self._states: dict[str, _RetentionState] = {}
        self._materialization_workspaces: dict[str, str] = {}
        self._db_path = Path(metadata_db_path) if metadata_db_path is not None else None
        if self._db_path is not None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize_database()
            self._load_states()

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise RuntimeError("retention metadata persistence is not configured")
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_retention_state (
                        workspace_id TEXT PRIMARY KEY,
                        retention TEXT NOT NULL,
                        expires_at TEXT,
                        ever_materialized INTEGER NOT NULL,
                        deleted INTEGER NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize workspace retention metadata",
            ) from exc

    def _load_states(self) -> None:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM workspace_retention_state ORDER BY workspace_id"
                ).fetchall()
            for row in rows:
                workspace_id = str(row["workspace_id"])
                validate_id(workspace_id, "workspace")
                expires_raw = row["expires_at"]
                expires_at = datetime.fromisoformat(str(expires_raw)) if expires_raw else None
                if expires_at is not None:
                    _aware(expires_at, "stored expires_at")
                self._states[workspace_id] = _RetentionState(
                    retention=WorkspaceRetention(str(row["retention"])),
                    expires_at=expires_at,
                    ever_materialized=bool(row["ever_materialized"]),
                    deleted=bool(row["deleted"]),
                )
        except (sqlite3.Error, ValueError, TypeError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored workspace retention metadata is invalid",
            ) from exc

    def _persist_states(self) -> None:
        if self._db_path is None:
            return
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM workspace_retention_state")
                for workspace_id, state in self._states.items():
                    connection.execute(
                        """
                        INSERT INTO workspace_retention_state (
                            workspace_id, retention, expires_at, ever_materialized, deleted
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            workspace_id,
                            state.retention.value,
                            state.expires_at.isoformat() if state.expires_at else None,
                            int(state.ever_materialized),
                            int(state.deleted),
                        ),
                    )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist workspace retention metadata",
            ) from exc

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
        if retention is WorkspaceRetention.UNTIL:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "retention=until requires set_retention with an explicit expires_at",
            )
        workspace = await self._delegate.create_workspace(
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
        async with self._lock:
            self._states[workspace.id] = _RetentionState(
                retention=retention,
                expires_at=workspace.expires_at,
            )
            self._persist_states()
        return self._overlay(workspace, self._states[workspace.id])

    async def get_workspace(self, workspace_id: str) -> Workspace:
        workspace = await self._delegate.get_workspace(workspace_id)
        state = await self._state_for(workspace)
        if state.deleted:
            raise ContractError(ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}")
        return self._overlay(workspace, state)

    async def list_workspaces(self, *, project_id: str | None = None) -> tuple[Workspace, ...]:
        values = await self._delegate.list_workspaces(project_id=project_id)
        result: list[Workspace] = []
        for workspace in values:
            state = await self._state_for(workspace)
            if not state.deleted:
                result.append(self._overlay(workspace, state))
        return tuple(result)

    async def get_snapshot(self, snapshot_id: str) -> WorkspaceSnapshot:
        return await self._delegate.get_snapshot(snapshot_id)

    async def create_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        await self.get_workspace(workspace_id)
        return await self._delegate.create_snapshot(workspace_id)

    async def materialize(
        self,
        workspace_id: str,
        context: DataAccessContext,
        *,
        snapshot_id: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
    ) -> WorkspaceMaterialization:
        await self.get_workspace(workspace_id)
        materialization = await self._delegate.materialize(
            workspace_id,
            context,
            snapshot_id=snapshot_id,
            task_id=task_id,
            run_id=run_id,
        )
        async with self._lock:
            state = self._states.get(workspace_id)
            if state is None:
                base = await self._delegate.get_workspace(workspace_id)
                state = self._state_from_workspace(base)
            self._states[workspace_id] = replace(
                state,
                ever_materialized=True,
                active_materializations=state.active_materializations + 1,
            )
            self._materialization_workspaces[materialization.id] = workspace_id
            self._persist_states()
        return materialization

    async def capture_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
    ) -> WorkspaceChangeSet:
        return await self._delegate.capture_changes(materialization_id, context)

    async def commit_changes(
        self,
        materialization_id: str,
        context: DataAccessContext,
        *,
        expected_revision: int,
    ) -> WorkspaceSnapshot:
        return await self._delegate.commit_changes(
            materialization_id,
            context,
            expected_revision=expected_revision,
        )

    async def release_materialization(
        self,
        materialization_id: str,
        outcome: MaterializationOutcome,
    ) -> None:
        await self._delegate.release_materialization(materialization_id, outcome)
        async with self._lock:
            self._finish_materialization(materialization_id)
            self._persist_states()

    async def cleanup(self) -> CleanupReport:
        report = await self._delegate.cleanup()
        reconciled = (*report.removed_materialization_ids, *report.missing_materialization_ids)
        if reconciled:
            async with self._lock:
                for materialization_id in reconciled:
                    self._finish_materialization(materialization_id)
                self._persist_states()
        return report

    async def set_retention(
        self,
        workspace_id: str,
        retention: WorkspaceRetention,
        *,
        expires_at: datetime | None = None,
    ) -> Workspace:
        workspace = await self._delegate.get_workspace(workspace_id)
        if retention is WorkspaceRetention.UNTIL:
            if expires_at is None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "retention=until requires expires_at",
                )
            _aware(expires_at, "expires_at")
        elif expires_at is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "expires_at is only valid with retention=until",
            )
        async with self._lock:
            current = self._states.get(workspace_id) or self._state_from_workspace(workspace)
            if current.deleted:
                raise ContractError(ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}")
            state = replace(current, retention=retention, expires_at=expires_at)
            self._states[workspace_id] = state
            self._persist_states()
        return self._overlay(workspace, state)

    async def enforce_retention(
        self,
        *,
        now: datetime | None = None,
        guard: WorkspaceRetentionGuard | None = None,
    ) -> WorkspaceRetentionReport:
        effective_now = _aware(now or _utc_now(), "now")
        deleted: list[str] = []
        retained: list[str] = []
        deferred: list[str] = []
        failed: list[str] = []

        for workspace in await self._delegate.list_workspaces():
            try:
                state = await self._state_for(workspace)
                if state.deleted:
                    continue
                managed = self._overlay(workspace, state)
                eligible = self._eligible(managed, state, effective_now)
                if not eligible:
                    retained.append(workspace.id)
                    continue
                if (
                    state.active_materializations > 0
                    or workspace.active_task_ids
                    or workspace.active_run_ids
                ):
                    deferred.append(workspace.id)
                    continue
                if guard is not None and not await guard.allow_cleanup(managed):
                    deferred.append(workspace.id)
                    continue
                async with self._lock:
                    latest = self._states.get(workspace.id) or state
                    if latest.active_materializations > 0:
                        deferred.append(workspace.id)
                        continue
                    self._states[workspace.id] = replace(latest, deleted=True)
                    self._persist_states()
                deleted.append(workspace.id)
            except Exception:
                failed.append(workspace.id)

        return WorkspaceRetentionReport(
            deleted_workspace_ids=tuple(sorted(deleted)),
            retained_workspace_ids=tuple(sorted(retained)),
            deferred_workspace_ids=tuple(sorted(deferred)),
            failed_workspace_ids=tuple(sorted(failed)),
        )

    async def _state_for(self, workspace: Workspace) -> _RetentionState:
        async with self._lock:
            state = self._states.get(workspace.id)
            if state is None:
                state = self._state_from_workspace(workspace)
                self._states[workspace.id] = state
                self._persist_states()
            return state

    def _finish_materialization(self, materialization_id: str) -> None:
        workspace_id = self._materialization_workspaces.pop(materialization_id, None)
        if workspace_id is None:
            return
        state = self._states.get(workspace_id)
        if state is None:
            return
        self._states[workspace_id] = replace(
            state,
            active_materializations=max(0, state.active_materializations - 1),
        )

    @staticmethod
    def _state_from_workspace(workspace: Workspace) -> _RetentionState:
        return _RetentionState(
            retention=workspace.retention,
            expires_at=workspace.expires_at,
            ever_materialized=workspace.last_used_at > workspace.created_at,
            deleted=workspace.status is WorkspaceStatus.DELETED,
        )

    @staticmethod
    def _overlay(workspace: Workspace, state: _RetentionState) -> Workspace:
        return replace(
            workspace,
            retention=state.retention,
            expires_at=state.expires_at,
            status=WorkspaceStatus.DELETED if state.deleted else workspace.status,
        )

    @staticmethod
    def _eligible(
        workspace: Workspace,
        state: _RetentionState,
        now: datetime,
    ) -> bool:
        if state.retention is WorkspaceRetention.PERSISTENT:
            return False
        if state.retention is WorkspaceRetention.EPHEMERAL:
            return state.ever_materialized
        if state.expires_at is None:
            return False
        return state.expires_at <= now
