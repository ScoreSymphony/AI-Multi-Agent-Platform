"""Persistence for immutable EvalManifest evidence."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .reproducibility import EvalManifest, decode_manifest, encode_manifest


class EvalManifestRepository(Protocol):
    """Persistence boundary for one immutable reproducibility manifest per EvaluationRun."""

    def save_manifest(self, manifest: EvalManifest) -> None: ...

    def get_manifest(self, evaluation_run_id: str) -> EvalManifest | None: ...


class InMemoryEvalManifestRepository:
    """Thread-safe reference manifest repository for tests and local execution."""

    def __init__(self) -> None:
        self._manifests: dict[str, EvalManifest] = {}
        self._lock = RLock()

    def save_manifest(self, manifest: EvalManifest) -> None:
        with self._lock:
            existing = self._manifests.get(manifest.evaluation_run_id)
            if existing is not None and existing.manifest_digest != manifest.manifest_digest:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "evaluation run already has a different immutable EvalManifest",
                )
            self._manifests[manifest.evaluation_run_id] = manifest

    def get_manifest(self, evaluation_run_id: str) -> EvalManifest | None:
        with self._lock:
            return self._manifests.get(evaluation_run_id)


class SqliteEvalManifestRepository:
    """Restart-safe manifest persistence, colocatable with the Evaluation SQLite database."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_manifests (
                        evaluation_run_id TEXT PRIMARY KEY,
                        manifest_id TEXT NOT NULL UNIQUE,
                        manifest_digest TEXT NOT NULL,
                        manifest_json TEXT NOT NULL
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize EvalManifest storage",
            ) from exc

    def save_manifest(self, manifest: EvalManifest) -> None:
        raw = encode_manifest(manifest)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT manifest_digest FROM evaluation_manifests WHERE evaluation_run_id = ?",
                    (manifest.evaluation_run_id,),
                ).fetchone()
                if row is not None and str(row["manifest_digest"]) != manifest.manifest_digest:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "evaluation run already has a different immutable EvalManifest",
                    )
                connection.execute(
                    """
                    INSERT INTO evaluation_manifests(
                        evaluation_run_id, manifest_id, manifest_digest, manifest_json
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(evaluation_run_id) DO NOTHING
                    """,
                    (
                        manifest.evaluation_run_id,
                        manifest.manifest_id,
                        manifest.manifest_digest,
                        raw,
                    ),
                )
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist EvalManifest",
            ) from exc

    def get_manifest(self, evaluation_run_id: str) -> EvalManifest | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT manifest_json FROM evaluation_manifests WHERE evaluation_run_id = ?",
                    (evaluation_run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read EvalManifest") from exc
        return None if row is None else decode_manifest(str(row["manifest_json"]))
