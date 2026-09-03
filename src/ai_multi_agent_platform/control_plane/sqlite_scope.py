"""Durable SQLite implementation of the #32 project/workspace identity store."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Project, new_id, validate_id

from .models import OwnerType, WorkspaceIdentity
from .service import ScopeStore


class SqliteScopeStore(ScopeStore):
    """Persist canonical Project identities and baseline idempotency across restarts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        super().__init__()
        self._load()

    def create_project(
        self,
        *,
        key: str,
        name: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
    ) -> Project:
        existing = self._commands.get(("project.create", key))
        if existing is not None:
            return self.get_project(existing)
        canonical_id = project_id or new_id("project")
        validate_id(canonical_id, "project")
        if canonical_id in self._projects:
            raise ContractError(ErrorCode.CONFLICT, f"project already exists: {canonical_id}")
        project = Project(
            id=canonical_id,
            name=name,
            owner_ref=OwnerRef(type=owner_type, id=owner_id),
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scope_projects (project_id, name, owner_type, owner_id, created_at, "
                "updated_at, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.owner_ref.type,
                    project.owner_ref.id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.schema_version,
                ),
            )
            connection.execute(
                "INSERT INTO scope_commands (command, idempotency_key, result_id) VALUES (?, ?, ?)",
                ("project.create", key, project.id),
            )
        self._projects[project.id] = project
        self._commands[("project.create", key)] = project.id
        return project

    def create_workspace(
        self,
        *,
        key: str,
        project_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceIdentity:
        existing = self._commands.get(("workspace.create", key))
        if existing is not None:
            return self.get_workspace(existing)
        project = self.get_project(project_id)
        workspace = WorkspaceIdentity(
            id=workspace_id or "",
            project_id=project_id,
            owner_type=project.owner_ref.type,
            owner_id=project.owner_ref.id,
        )
        if workspace.id in self._workspaces:
            raise ContractError(ErrorCode.CONFLICT, f"workspace already exists: {workspace.id}")
        assert workspace.created_at is not None
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scope_workspaces (workspace_id, project_id, owner_type, owner_id, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    workspace.id,
                    workspace.project_id,
                    workspace.owner_type,
                    workspace.owner_id,
                    workspace.created_at.isoformat(),
                ),
            )
            connection.execute(
                "INSERT INTO scope_commands (command, idempotency_key, result_id) VALUES (?, ?, ?)",
                ("workspace.create", key, workspace.id),
            )
        self._workspaces[workspace.id] = workspace
        self._commands[("workspace.create", key)] = workspace.id
        return workspace

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS scope_projects (
                    project_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scope_workspaces (
                    workspace_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    owner_type TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(project_id) REFERENCES scope_projects(project_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS scope_commands (
                    command TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    PRIMARY KEY(command, idempotency_key)
                );
                """
            )

    def _load(self) -> None:
        with self._connect() as connection:
            project_rows = connection.execute(
                "SELECT project_id, name, owner_type, owner_id, created_at, "
                "updated_at, schema_version FROM scope_projects"
            ).fetchall()
            workspace_rows = connection.execute(
                "SELECT workspace_id, project_id, owner_type, owner_id, created_at "
                "FROM scope_workspaces"
            ).fetchall()
            command_rows = connection.execute(
                "SELECT command, idempotency_key, result_id FROM scope_commands"
            ).fetchall()

        for row in project_rows:
            owner_type = _owner_type(str(row[2]))
            project = Project(
                id=str(row[0]),
                name=str(row[1]),
                owner_ref=OwnerRef(type=owner_type, id=str(row[3])),
                created_at=_datetime(str(row[4])),
                updated_at=_datetime(str(row[5])),
                schema_version=str(row[6]),
            )
            self._projects[project.id] = project
        for row in workspace_rows:
            workspace = WorkspaceIdentity(
                id=str(row[0]),
                project_id=str(row[1]),
                owner_type=_owner_type(str(row[2])),
                owner_id=str(row[3]),
                created_at=_datetime(str(row[4])),
            )
            self._workspaces[workspace.id] = workspace
        for row in command_rows:
            self._commands[(str(row[0]), str(row[1]))] = str(row[2])


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("persisted scope timestamp must be timezone-aware")
    return parsed


def _owner_type(value: str) -> OwnerType:
    if value not in {"user", "organization", "team", "service"}:
        raise ValueError(f"persisted scope owner type is invalid: {value}")
    return value  # type: ignore[return-value]
