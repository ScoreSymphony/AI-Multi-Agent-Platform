"""Durable SQLite implementation of the canonical Project/Workspace scope store."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import (
    ExternalRef,
    OwnerRef,
    Project,
    Provenance,
    new_id,
    validate_id,
)

from .models import OwnerType, WorkspaceIdentity
from .service import ScopeStore


class SqliteScopeStore(ScopeStore):
    """Persist complete canonical Project snapshots and Workspace identities."""

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
        self._validate_project_snapshot(project)
        self._insert_project(project, command="project.create", key=key)
        self._projects[project.id] = project
        self._commands[("project.create", key)] = project.id
        return project

    def store_project_snapshot(self, *, key: str, project: Project) -> Project:
        if not key.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "project snapshot key must not be blank")
        self._validate_project_snapshot(project)
        command_key = ("project.snapshot.store", key)
        existing = self._commands.get(command_key)
        if existing is not None:
            stored = self.get_project(existing)
            if stored != project:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "project snapshot idempotency key is bound to a different Project",
                )
            return stored
        if project.id in self._projects:
            raise ContractError(ErrorCode.CONFLICT, f"project already exists: {project.id}")
        self._insert_project(project, command=command_key[0], key=key)
        self._projects[project.id] = project
        self._commands[command_key] = project.id
        return project

    def compensate_project(
        self,
        project_id: str,
        *,
        external_dependencies: tuple[str, ...] | None = None,
    ) -> Project:
        project = self._require_project_compensation_safe(
            project_id,
            external_dependencies=external_dependencies,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workspace_row = connection.execute(
                "SELECT workspace_id FROM scope_workspaces WHERE project_id = ? LIMIT 1",
                (project_id,),
            ).fetchone()
            if workspace_row is not None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "project compensation refused because Workspace dependencies exist",
                    details={"dependencies": [str(workspace_row[0])]},
                )
            deleted = connection.execute(
                "DELETE FROM scope_projects WHERE project_id = ?",
                (project_id,),
            )
            if deleted.rowcount != 1:
                raise ContractError(ErrorCode.NOT_FOUND, f"project not found: {project_id}")
            connection.execute(
                "DELETE FROM scope_commands WHERE result_id = ? "
                "AND command IN ('project.create', 'project.snapshot.store')",
                (project_id,),
            )
        del self._projects[project_id]
        self._forget_project_commands(project_id)
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

    def _insert_project(self, project: Project, *, command: str, key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO scope_projects (project_id, name, owner_type, owner_id, created_at, "
                "updated_at, schema_version, provenance_json, external_refs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.owner_ref.type,
                    project.owner_ref.id,
                    project.created_at.isoformat(),
                    project.updated_at.isoformat(),
                    project.schema_version,
                    _provenance_json(project.provenance),
                    _external_refs_json(project.external_refs),
                ),
            )
            connection.execute(
                "INSERT INTO scope_commands (command, idempotency_key, result_id) VALUES (?, ?, ?)",
                (command, key, project.id),
            )

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
                    schema_version TEXT NOT NULL,
                    provenance_json TEXT NOT NULL DEFAULT 'null',
                    external_refs_json TEXT NOT NULL DEFAULT '[]'
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
            columns = {
                str(row[1])
                for row in connection.execute("PRAGMA table_info(scope_projects)").fetchall()
            }
            if "provenance_json" not in columns:
                connection.execute(
                    "ALTER TABLE scope_projects ADD COLUMN provenance_json "
                    "TEXT NOT NULL DEFAULT 'null'"
                )
            if "external_refs_json" not in columns:
                connection.execute(
                    "ALTER TABLE scope_projects ADD COLUMN external_refs_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )

    def _load(self) -> None:
        with self._connect() as connection:
            project_rows = connection.execute(
                "SELECT project_id, name, owner_type, owner_id, created_at, updated_at, "
                "schema_version, provenance_json, external_refs_json FROM scope_projects"
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
                provenance=_provenance_from_json(str(row[7])),
                external_refs=_external_refs_from_json(str(row[8])),
            )
            self._validate_project_snapshot(project)
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


def _json_dump(value: JsonValue) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _json_safe(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("non-finite metadata number")
        return value
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("metadata keys must be strings")
            converted[key] = _json_safe(item)
        return converted
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    raise TypeError(f"unsupported metadata value: {type(value).__name__}")


def _provenance_json(provenance: Provenance | None) -> str:
    if provenance is None:
        return "null"
    return _json_dump(
        {
            "source": provenance.source,
            "actor_ref": provenance.actor_ref,
            "details": _json_safe(provenance.details),
        }
    )


def _provenance_from_json(raw: str) -> Provenance | None:
    try:
        value = json.loads(raw)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("provenance must be an object or null")
        source = value.get("source")
        actor_ref = value.get("actor_ref")
        details = value.get("details", {})
        if not isinstance(source, str) or not source.strip():
            raise ValueError("provenance source is invalid")
        if actor_ref is not None and not isinstance(actor_ref, str):
            raise ValueError("provenance actor_ref is invalid")
        if not isinstance(details, dict):
            raise ValueError("provenance details are invalid")
        return Provenance(source=source, actor_ref=actor_ref, details=details)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "persisted Project provenance is invalid",
        ) from exc


def _external_refs_json(external_refs: tuple[ExternalRef, ...]) -> str:
    return _json_dump(
        [{"system": item.system, "kind": item.kind, "value": item.value} for item in external_refs]
    )


def _external_refs_from_json(raw: str) -> tuple[ExternalRef, ...]:
    try:
        value = json.loads(raw)
        if not isinstance(value, list):
            raise ValueError("external refs must be an array")
        refs: list[ExternalRef] = []
        for item in value:
            if not isinstance(item, dict):
                raise ValueError("external ref must be an object")
            system = item.get("system")
            kind = item.get("kind")
            ref_value = item.get("value")
            if not all(isinstance(part, str) for part in (system, kind, ref_value)):
                raise ValueError("external ref fields are invalid")
            refs.append(
                ExternalRef(
                    system=cast(str, system),
                    kind=cast(str, kind),
                    value=cast(str, ref_value),
                )
            )
        return tuple(refs)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "persisted Project external references are invalid",
        ) from exc


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("persisted scope timestamp must be timezone-aware")
    return parsed


def _owner_type(value: str) -> OwnerType:
    if value not in {"user", "organization", "team", "service"}:
        raise ValueError(f"persisted scope owner type is invalid: {value}")
    return cast(OwnerType, value)
