"""Restart-safe SQLite metadata persistence for the local workspace provider."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    MaterializationOutcome,
    Workspace,
    WorkspaceAccessMode,
    WorkspaceFile,
    WorkspaceMaterialization,
    WorkspaceRetention,
    WorkspaceSnapshot,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceStatus,
    WorkspaceType,
)
from .reference import LocalWorkspaceProvider

OwnerType = Literal["user", "organization", "team", "service"]


def _dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load_list(value: str, field: str) -> list[object]:
    loaded = json.loads(value)
    if not isinstance(loaded, list):
        raise ValueError(f"stored {field} must be a JSON array")
    return cast(list[object], loaded)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored workspace timestamp must be timezone-aware")
    return parsed


def _optional_time(value: str | None) -> datetime | None:
    return None if value is None else _time(value)


def _source_to_json(source: WorkspaceSourceRef) -> dict[str, JsonValue]:
    return {
        "kind": source.kind.value,
        "ref": source.ref,
        "revision": source.revision,
        "checksum": source.checksum,
        "metadata": dict(source.metadata),
    }


def _source_from_json(value: object) -> WorkspaceSourceRef:
    if not isinstance(value, dict):
        raise ValueError("stored workspace source must be a JSON object")
    raw = cast(dict[str, object], value)
    kind = raw.get("kind")
    ref = raw.get("ref")
    revision = raw.get("revision")
    checksum = raw.get("checksum")
    metadata = raw.get("metadata", {})
    if not isinstance(kind, str) or not isinstance(ref, str):
        raise ValueError("stored workspace source kind/ref are invalid")
    if revision is not None and not isinstance(revision, str):
        raise ValueError("stored workspace source revision is invalid")
    if checksum is not None and not isinstance(checksum, str):
        raise ValueError("stored workspace source checksum is invalid")
    if not isinstance(metadata, dict):
        raise ValueError("stored workspace source metadata is invalid")
    return WorkspaceSourceRef(
        kind=WorkspaceSourceKind(kind),
        ref=ref,
        revision=revision,
        checksum=checksum,
        metadata=cast(dict[str, JsonValue], metadata),
    )


def _file_to_json(entry: WorkspaceFile) -> dict[str, str]:
    return {
        "relative_path": entry.relative_path,
        "file_id": entry.file_id,
        "sha256": entry.sha256,
    }


def _file_from_json(value: object) -> WorkspaceFile:
    if not isinstance(value, dict):
        raise ValueError("stored workspace file must be a JSON object")
    raw = cast(dict[str, object], value)
    relative_path = raw.get("relative_path")
    file_id = raw.get("file_id")
    sha256 = raw.get("sha256")
    if not all(isinstance(item, str) for item in (relative_path, file_id, sha256)):
        raise ValueError("stored workspace file fields are invalid")
    return WorkspaceFile(
        relative_path=cast(str, relative_path),
        file_id=cast(str, file_id),
        sha256=cast(str, sha256),
    )


class SqliteWorkspaceProvider(LocalWorkspaceProvider):
    """Local provider whose canonical workspace metadata survives process restarts."""

    def __init__(self, root: str | Path, files: FileProvider, db_path: str | Path) -> None:
        super().__init__(root, files)
        self._workspace_db_path = Path(db_path)
        self._workspace_db_path.parent.mkdir(parents=True, exist_ok=True)
        self._persistence_lock = asyncio.Lock()
        self._initialize_database()
        self._load_state()

    def _connect_workspace_db(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._workspace_db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_database(self) -> None:
        try:
            with self._connect_workspace_db() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS workspace_metadata (
                        workspace_id TEXT PRIMARY KEY,
                        project_id TEXT NOT NULL,
                        owner_type TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        workspace_type TEXT NOT NULL,
                        access_mode TEXT NOT NULL,
                        status TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        source_refs_json TEXT NOT NULL,
                        base_snapshot_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        retention TEXT NOT NULL,
                        expires_at TEXT,
                        policy_labels_json TEXT NOT NULL,
                        active_task_ids_json TEXT NOT NULL,
                        active_run_ids_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS workspace_snapshots (
                        snapshot_id TEXT PRIMARY KEY,
                        workspace_id TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        files_json TEXT NOT NULL,
                        content_checksum TEXT NOT NULL,
                        source_refs_json TEXT NOT NULL,
                        parent_snapshot_id TEXT,
                        source_revision TEXT,
                        artifact_ids_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS workspace_heads (
                        workspace_id TEXT PRIMARY KEY,
                        snapshot_id TEXT NOT NULL
                    );
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize workspace metadata database",
            ) from exc

    def _load_state(self) -> None:
        try:
            with self._connect_workspace_db() as connection:
                workspace_rows = connection.execute(
                    "SELECT * FROM workspace_metadata ORDER BY workspace_id"
                ).fetchall()
                snapshot_rows = connection.execute(
                    "SELECT * FROM workspace_snapshots ORDER BY snapshot_id"
                ).fetchall()
                head_rows = connection.execute(
                    "SELECT workspace_id, snapshot_id FROM workspace_heads"
                ).fetchall()
            workspaces = {
                cast(str, row["workspace_id"]): self._workspace_from_row(row)
                for row in workspace_rows
            }
            snapshots = {
                cast(str, row["snapshot_id"]): self._snapshot_from_row(row)
                for row in snapshot_rows
            }
            heads = {
                cast(str, row["workspace_id"]): cast(str, row["snapshot_id"])
                for row in head_rows
            }
            for workspace_id, snapshot_id in heads.items():
                workspace = workspaces.get(workspace_id)
                snapshot = snapshots.get(snapshot_id)
                if workspace is None or snapshot is None or snapshot.workspace_id != workspace_id:
                    raise ValueError("stored workspace head references invalid canonical state")
                if workspace.base_snapshot_id != snapshot_id:
                    raise ValueError("stored workspace base snapshot differs from workspace head")
            for workspace in workspaces.values():
                if workspace.base_snapshot_id is None or workspace.id not in heads:
                    raise ValueError("stored workspace is missing a canonical head snapshot")
            had_active_refs = any(
                workspace.active_task_ids or workspace.active_run_ids
                for workspace in workspaces.values()
            )
            self._workspaces = {
                workspace_id: replace(workspace, active_task_ids=(), active_run_ids=())
                for workspace_id, workspace in workspaces.items()
            }
            self._snapshots = snapshots
            self._heads = heads
            if had_active_refs:
                self._persist_state()
        except (sqlite3.Error, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored workspace metadata is invalid",
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
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            workspace = await super().create_workspace(
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
            self._persist_or_restore(checkpoint)
            return workspace

    async def create_snapshot(self, workspace_id: str) -> WorkspaceSnapshot:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            snapshot = await super().create_snapshot(workspace_id)
            self._persist_or_restore(checkpoint)
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
            materialization = await super().materialize(
                workspace_id,
                context,
                snapshot_id=snapshot_id,
                task_id=task_id,
                run_id=run_id,
            )
            self._persist_or_restore(checkpoint)
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
            snapshot = await super().commit_changes(
                materialization_id,
                context,
                expected_revision=expected_revision,
            )
            self._persist_or_restore(checkpoint)
            return snapshot

    async def release_materialization(
        self,
        materialization_id: str,
        outcome: MaterializationOutcome,
    ) -> None:
        async with self._persistence_lock:
            checkpoint = self._checkpoint()
            await super().release_materialization(materialization_id, outcome)
            self._persist_or_restore(checkpoint)

    def _checkpoint(
        self,
    ) -> tuple[dict[str, Workspace], dict[str, WorkspaceSnapshot], dict[str, str]]:
        return dict(self._workspaces), dict(self._snapshots), dict(self._heads)

    def _persist_or_restore(
        self,
        checkpoint: tuple[dict[str, Workspace], dict[str, WorkspaceSnapshot], dict[str, str]],
    ) -> None:
        try:
            self._persist_state()
        except ContractError:
            self._workspaces, self._snapshots, self._heads = checkpoint
            raise

    def _persist_state(self) -> None:
        try:
            with self._connect_workspace_db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM workspace_heads")
                connection.execute("DELETE FROM workspace_snapshots")
                connection.execute("DELETE FROM workspace_metadata")
                for workspace in self._workspaces.values():
                    connection.execute(
                        """
                        INSERT INTO workspace_metadata (
                            workspace_id, project_id, owner_type, owner_id, workspace_type,
                            access_mode, status, revision, source_refs_json, base_snapshot_id,
                            created_at, updated_at, last_used_at, retention, expires_at,
                            policy_labels_json, active_task_ids_json, active_run_ids_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            workspace.id,
                            workspace.project_id,
                            workspace.owner_ref.type,
                            workspace.owner_ref.id,
                            workspace.workspace_type.value,
                            workspace.access_mode.value,
                            workspace.status.value,
                            workspace.revision,
                            _dump([_source_to_json(item) for item in workspace.source_refs]),
                            workspace.base_snapshot_id,
                            workspace.created_at.isoformat(),
                            workspace.updated_at.isoformat(),
                            workspace.last_used_at.isoformat(),
                            workspace.retention.value,
                            workspace.expires_at.isoformat() if workspace.expires_at else None,
                            _dump(list(workspace.policy_labels)),
                            _dump(list(workspace.active_task_ids)),
                            _dump(list(workspace.active_run_ids)),
                        ),
                    )
                for snapshot in self._snapshots.values():
                    connection.execute(
                        """
                        INSERT INTO workspace_snapshots (
                            snapshot_id, workspace_id, revision, files_json, content_checksum,
                            source_refs_json, parent_snapshot_id, source_revision,
                            artifact_ids_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot.id,
                            snapshot.workspace_id,
                            snapshot.revision,
                            _dump([_file_to_json(item) for item in snapshot.files]),
                            snapshot.content_checksum,
                            _dump([_source_to_json(item) for item in snapshot.source_refs]),
                            snapshot.parent_snapshot_id,
                            snapshot.source_revision,
                            _dump(list(snapshot.artifact_ids)),
                            snapshot.created_at.isoformat(),
                        ),
                    )
                for workspace_id, snapshot_id in self._heads.items():
                    connection.execute(
                        "INSERT INTO workspace_heads (workspace_id, snapshot_id) VALUES (?, ?)",
                        (workspace_id, snapshot_id),
                    )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist workspace metadata",
            ) from exc

    @staticmethod
    def _workspace_from_row(row: sqlite3.Row) -> Workspace:
        source_refs = tuple(
            _source_from_json(item)
            for item in _load_list(cast(str, row["source_refs_json"]), "workspace source refs")
        )
        return Workspace(
            id=cast(str, row["workspace_id"]),
            project_id=cast(str, row["project_id"]),
            owner_ref=OwnerRef(
                type=cast(OwnerType, row["owner_type"]),
                id=cast(str, row["owner_id"]),
            ),
            workspace_type=WorkspaceType(cast(str, row["workspace_type"])),
            access_mode=WorkspaceAccessMode(cast(str, row["access_mode"])),
            status=WorkspaceStatus(cast(str, row["status"])),
            revision=cast(int, row["revision"]),
            source_refs=source_refs,
            base_snapshot_id=cast(str | None, row["base_snapshot_id"]),
            created_at=_time(cast(str, row["created_at"])),
            updated_at=_time(cast(str, row["updated_at"])),
            last_used_at=_time(cast(str, row["last_used_at"])),
            retention=WorkspaceRetention(cast(str, row["retention"])),
            expires_at=_optional_time(cast(str | None, row["expires_at"])),
            policy_labels=SqliteWorkspaceProvider._string_tuple(
                cast(str, row["policy_labels_json"]), "workspace policy labels"
            ),
            active_task_ids=SqliteWorkspaceProvider._string_tuple(
                cast(str, row["active_task_ids_json"]), "workspace active task IDs"
            ),
            active_run_ids=SqliteWorkspaceProvider._string_tuple(
                cast(str, row["active_run_ids_json"]), "workspace active run IDs"
            ),
        )

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            id=cast(str, row["snapshot_id"]),
            workspace_id=cast(str, row["workspace_id"]),
            revision=cast(int, row["revision"]),
            files=tuple(
                _file_from_json(item)
                for item in _load_list(
                    cast(str, row["files_json"]), "workspace snapshot files"
                )
            ),
            content_checksum=cast(str, row["content_checksum"]),
            source_refs=tuple(
                _source_from_json(item)
                for item in _load_list(
                    cast(str, row["source_refs_json"]), "workspace snapshot source refs"
                )
            ),
            parent_snapshot_id=cast(str | None, row["parent_snapshot_id"]),
            source_revision=cast(str | None, row["source_revision"]),
            artifact_ids=SqliteWorkspaceProvider._string_tuple(
                cast(str, row["artifact_ids_json"]), "workspace snapshot artifact IDs"
            ),
            created_at=_time(cast(str, row["created_at"])),
        )

    @staticmethod
    def _string_tuple(value: str, field: str) -> tuple[str, ...]:
        items = _load_list(value, field)
        if not all(isinstance(item, str) for item in items):
            raise ValueError(f"stored {field} must contain only strings")
        return tuple(cast(list[str], items))
