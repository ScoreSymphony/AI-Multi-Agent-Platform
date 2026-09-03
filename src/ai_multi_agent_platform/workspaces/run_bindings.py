"""Durable canonical bindings between Runs and exact Workspace snapshots."""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import validate_id

from .models import validate_sha256


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored run workspace binding timestamp must be timezone-aware",
        )
    return parsed


@dataclass(frozen=True, slots=True)
class RunWorkspaceBinding:
    """Exact, immutable workspace input selected for one canonical Run."""

    run_id: str
    task_id: str
    workspace_id: str
    workspace_snapshot_id: str
    content_checksum: str
    created_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        validate_id(self.workspace_id, "workspace")
        validate_id(self.workspace_snapshot_id, "workspace_snapshot")
        object.__setattr__(self, "content_checksum", validate_sha256(self.content_checksum))
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("run workspace binding created_at must be timezone-aware")

    def same_target(self, other: RunWorkspaceBinding) -> bool:
        return (
            self.run_id == other.run_id
            and self.task_id == other.task_id
            and self.workspace_id == other.workspace_id
            and self.workspace_snapshot_id == other.workspace_snapshot_id
            and self.content_checksum == other.content_checksum
        )


class RunWorkspaceBindingRepository(ABC):
    """Canonical persistence seam for immutable Run workspace input bindings."""

    @abstractmethod
    async def bind(self, binding: RunWorkspaceBinding) -> RunWorkspaceBinding: ...

    @abstractmethod
    async def get(self, run_id: str) -> RunWorkspaceBinding | None: ...


class InMemoryRunWorkspaceBindingRepository(RunWorkspaceBindingRepository):
    def __init__(self) -> None:
        self._bindings: dict[str, RunWorkspaceBinding] = {}

    async def bind(self, binding: RunWorkspaceBinding) -> RunWorkspaceBinding:
        existing = self._bindings.get(binding.run_id)
        if existing is not None:
            if not existing.same_target(binding):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"run already has a different workspace binding: {binding.run_id}",
                )
            return existing
        self._bindings[binding.run_id] = binding
        return binding

    async def get(self, run_id: str) -> RunWorkspaceBinding | None:
        validate_id(run_id, "run")
        return self._bindings.get(run_id)


class SqliteRunWorkspaceBindingRepository(RunWorkspaceBindingRepository):
    """Restart-safe immutable Run input bindings stored in SQLite."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS run_workspace_bindings (
                        run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        workspace_id TEXT NOT NULL,
                        workspace_snapshot_id TEXT NOT NULL,
                        content_checksum TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize run workspace binding storage",
            ) from exc

    async def bind(self, binding: RunWorkspaceBinding) -> RunWorkspaceBinding:
        existing = await self.get(binding.run_id)
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
            raced = await self.get(binding.run_id)
            if raced is not None and raced.same_target(binding):
                return raced
            raise ContractError(
                ErrorCode.CONFLICT,
                f"run already has a different workspace binding: {binding.run_id}",
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist run workspace binding",
            ) from exc
        return binding

    async def get(self, run_id: str) -> RunWorkspaceBinding | None:
        validate_id(run_id, "run")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM run_workspace_bindings WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read run workspace binding",
            ) from exc
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
