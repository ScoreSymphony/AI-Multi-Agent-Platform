"""Durable reference persistence for repository Run provenance."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import RepositoryRunProvenance
from .service import RepositoryProvenanceStore


class SqliteRepositoryProvenanceStore(RepositoryProvenanceStore):
    """Restart-safe SQLite implementation of the repository provenance seam.

    Only canonical IDs, immutable revisions and artifact/resource references are persisted.
    Provider objects, local clone paths and credential material never cross this boundary.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS repository_run_provenance (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        repository_id TEXT NOT NULL,
                        input_revision TEXT NOT NULL,
                        output_key TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        UNIQUE(run_id, repository_id, input_revision, output_key)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_repository_run_provenance_run
                    ON repository_run_provenance(run_id, sequence)
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize repository provenance store",
            ) from exc

    def record(self, provenance: RepositoryRunProvenance) -> None:
        """Persist one exact provenance state idempotently."""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO repository_run_provenance(
                        run_id,
                        repository_id,
                        input_revision,
                        output_key,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        provenance.run_id,
                        provenance.repository_id,
                        provenance.input_revision,
                        provenance.output_revision or "",
                        _encode_provenance(provenance),
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist repository Run provenance",
            ) from exc

    def upsert(self, provenance: RepositoryRunProvenance) -> None:
        """Replace one Run/repository/input record as output evidence becomes available."""

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    DELETE FROM repository_run_provenance
                    WHERE run_id = ? AND repository_id = ? AND input_revision = ?
                    """,
                    (
                        provenance.run_id,
                        provenance.repository_id,
                        provenance.input_revision,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO repository_run_provenance(
                        run_id,
                        repository_id,
                        input_revision,
                        output_key,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        provenance.run_id,
                        provenance.repository_id,
                        provenance.input_revision,
                        provenance.output_revision or "",
                        _encode_provenance(provenance),
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to update repository Run provenance",
            ) from exc

    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload_json
                    FROM repository_run_provenance
                    WHERE run_id = ? AND repository_id = ?
                    ORDER BY sequence ASC
                    LIMIT 1
                    """,
                    (run_id, repository_id),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read repository Run provenance",
            ) from exc
        if row is None:
            return None
        return _decode_provenance(cast(str, row["payload_json"]))

    def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM repository_run_provenance
                    WHERE run_id = ?
                    ORDER BY sequence ASC
                    """,
                    (run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to list repository Run provenance",
            ) from exc
        return tuple(_decode_provenance(cast(str, row["payload_json"])) for row in rows)


def _encode_provenance(provenance: RepositoryRunProvenance) -> str:
    return json.dumps(
        {
            "run_id": provenance.run_id,
            "repository_id": provenance.repository_id,
            "input_revision": provenance.input_revision,
            "actor_ref": provenance.actor_ref,
            "agent_id": provenance.agent_id,
            "branch_ref": provenance.branch_ref,
            "output_revision": provenance.output_revision,
            "task_id": provenance.task_id,
            "diff_artifact_ids": list(provenance.diff_artifact_ids),
            "provider_resource_ids": list(provenance.provider_resource_ids),
            "recorded_at": provenance.recorded_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_provenance(payload: str) -> RepositoryRunProvenance:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository provenance is not valid JSON",
        ) from exc
    if not isinstance(raw, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository provenance must be a JSON object",
        )
    data = cast(dict[str, object], raw)
    try:
        recorded_at = datetime.fromisoformat(_required_string(data, "recorded_at"))
        return RepositoryRunProvenance(
            run_id=_required_string(data, "run_id"),
            repository_id=_required_string(data, "repository_id"),
            input_revision=_required_string(data, "input_revision"),
            actor_ref=_required_string(data, "actor_ref"),
            agent_id=_optional_string(data, "agent_id"),
            branch_ref=_optional_string(data, "branch_ref"),
            output_revision=_optional_string(data, "output_revision"),
            task_id=_optional_string(data, "task_id"),
            diff_artifact_ids=_string_tuple(data, "diff_artifact_ids"),
            provider_resource_ids=_string_tuple(data, "provider_resource_ids"),
            recorded_at=recorded_at,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "stored repository provenance violates the canonical contract",
        ) from exc


def _required_string(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"stored repository provenance field {key} must be a string")
    return value


def _optional_string(data: dict[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"stored repository provenance field {key} must be a string or null")
    return value


def _string_tuple(data: dict[str, object], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"stored repository provenance field {key} must be a string array")
    return tuple(cast(list[str], value))
