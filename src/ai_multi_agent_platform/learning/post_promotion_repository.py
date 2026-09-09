"""Durable recorder for derived post-promotion Learning Evaluation evidence (#595)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .runtime import (
    PostPromotionEvaluationOutcome,
    PostPromotionEvaluationRecord,
)


class SQLitePostPromotionEvaluationRecorder:
    """Restart-safe recorder with one canonical record per promoted target revision."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS learning_post_promotion_evaluations (
                    record_id TEXT PRIMARY KEY,
                    learning_candidate_id TEXT NOT NULL,
                    candidate_revision INTEGER NOT NULL,
                    target_revision INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    evaluation_run_ids_json TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (learning_candidate_id, target_revision)
                );

                CREATE INDEX IF NOT EXISTS idx_learning_post_eval_candidate
                    ON learning_post_promotion_evaluations(
                        learning_candidate_id,
                        target_revision,
                        created_at
                    );
                """
            )

    def store(self, record: PostPromotionEvaluationRecord) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT record_id FROM learning_post_promotion_evaluations "
                "WHERE learning_candidate_id = ? AND target_revision = ?",
                (record.learning_candidate_id, record.target_revision),
            ).fetchone()
            if existing is not None:
                return
            connection.execute(
                "INSERT INTO learning_post_promotion_evaluations("
                "record_id, learning_candidate_id, candidate_revision, target_revision, "
                "outcome, evaluation_run_ids_json, details_json, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.record_id,
                    record.learning_candidate_id,
                    record.candidate_revision,
                    record.target_revision,
                    record.outcome.value,
                    json.dumps(list(record.evaluation_run_ids), separators=(",", ":")),
                    json.dumps(dict(record.details), sort_keys=True, separators=(",", ":")),
                    record.created_at.isoformat(),
                ),
            )
        # Restore-integrity readers intentionally open immutable snapshots. Checkpoint after the
        # committed write so the canonical database file, not only its WAL, contains the record.
        # Post-promotion records are low-volume governance evidence, so this durability boundary is
        # preferable to allowing a validated backup/restore path to miss a just-committed record.
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def list_for_candidate(
        self,
        learning_candidate_id: str,
    ) -> tuple[PostPromotionEvaluationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM learning_post_promotion_evaluations "
                "WHERE learning_candidate_id = ? ORDER BY target_revision, created_at, record_id",
                (learning_candidate_id,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def record_for_promotion(
        self,
        learning_candidate_id: str,
        target_revision: int,
    ) -> PostPromotionEvaluationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM learning_post_promotion_evaluations "
                "WHERE learning_candidate_id = ? AND target_revision = ?",
                (learning_candidate_id, target_revision),
            ).fetchone()
        return None if row is None else _record_from_row(row)


def _record_from_row(row: sqlite3.Row) -> PostPromotionEvaluationRecord:
    run_ids_raw = json.loads(str(row["evaluation_run_ids_json"]))
    details_raw = json.loads(str(row["details_json"]))
    if not isinstance(run_ids_raw, list) or not all(
        isinstance(value, str) for value in run_ids_raw
    ):
        raise ValueError("stored post-promotion Evaluation run IDs are malformed")
    if not isinstance(details_raw, dict):
        raise ValueError("stored post-promotion Evaluation details are malformed")
    return PostPromotionEvaluationRecord(
        record_id=str(row["record_id"]),
        learning_candidate_id=str(row["learning_candidate_id"]),
        candidate_revision=int(row["candidate_revision"]),
        target_revision=int(row["target_revision"]),
        outcome=PostPromotionEvaluationOutcome(str(row["outcome"])),
        evaluation_run_ids=tuple(run_ids_raw),
        details=cast(dict[str, JsonValue], details_raw),
        created_at=datetime.fromisoformat(str(row["created_at"])),
    )


__all__ = ["SQLitePostPromotionEvaluationRecorder"]
