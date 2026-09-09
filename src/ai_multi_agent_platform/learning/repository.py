"""Durable append-only repositories for governed learning candidates (#595)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    FeedbackRecord,
    LearningCandidate,
    candidate_from_dict,
    candidate_to_dict,
    feedback_from_dict,
    feedback_to_dict,
)


class LearningRepository(Protocol):
    def create_feedback(
        self,
        feedback: FeedbackRecord,
        *,
        dedupe_key: str,
    ) -> tuple[FeedbackRecord, bool]: ...

    def get_feedback(self, feedback_id: str) -> FeedbackRecord: ...

    def list_feedback(self) -> tuple[FeedbackRecord, ...]: ...

    def create_candidate(
        self,
        candidate: LearningCandidate,
        *,
        dedupe_key: str,
    ) -> tuple[LearningCandidate, bool]: ...

    def append_candidate(
        self,
        candidate: LearningCandidate,
        *,
        expected_revision: int,
    ) -> LearningCandidate: ...

    def get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate: ...

    def list_candidates(self) -> tuple[LearningCandidate, ...]: ...

    def find_candidate_by_dedupe_key(self, dedupe_key: str) -> LearningCandidate | None: ...


class InMemoryLearningRepository:
    """Reference repository preserving immutable candidate revisions and feedback."""

    def __init__(self) -> None:
        self._feedback: dict[str, FeedbackRecord] = {}
        self._feedback_keys: dict[str, str] = {}
        self._candidates: dict[tuple[str, int], LearningCandidate] = {}
        self._candidate_keys: dict[str, str] = {}

    def create_feedback(
        self,
        feedback: FeedbackRecord,
        *,
        dedupe_key: str,
    ) -> tuple[FeedbackRecord, bool]:
        _require_key(dedupe_key, "feedback dedupe_key")
        existing_id = self._feedback_keys.get(dedupe_key)
        if existing_id is not None:
            return self._feedback[existing_id], False
        if feedback.feedback_id in self._feedback:
            existing = self._feedback[feedback.feedback_id]
            if existing.content_digest != feedback.content_digest:
                raise ContractError(ErrorCode.CONFLICT, "feedback ID already exists")
            return existing, False
        self._feedback[feedback.feedback_id] = feedback
        self._feedback_keys[dedupe_key] = feedback.feedback_id
        return feedback, True

    def get_feedback(self, feedback_id: str) -> FeedbackRecord:
        try:
            return self._feedback[feedback_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "feedback was not found") from exc

    def list_feedback(self) -> tuple[FeedbackRecord, ...]:
        return tuple(
            sorted(
                self._feedback.values(),
                key=lambda item: (item.created_at, item.feedback_id),
            )
        )

    def create_candidate(
        self,
        candidate: LearningCandidate,
        *,
        dedupe_key: str,
    ) -> tuple[LearningCandidate, bool]:
        _require_key(dedupe_key, "candidate dedupe_key")
        existing_id = self._candidate_keys.get(dedupe_key)
        if existing_id is not None:
            return self.get_candidate(existing_id), False
        if candidate.revision != 1:
            raise ContractError(
                ErrorCode.CONFLICT, "new learning candidate must start at revision 1"
            )
        key = (candidate.learning_candidate_id, candidate.revision)
        if key in self._candidates:
            existing = self._candidates[key]
            if existing.content_digest != candidate.content_digest:
                raise ContractError(
                    ErrorCode.CONFLICT, "learning candidate revision already exists"
                )
            return existing, False
        self._candidates[key] = candidate
        self._candidate_keys[dedupe_key] = candidate.learning_candidate_id
        return candidate, True

    def append_candidate(
        self,
        candidate: LearningCandidate,
        *,
        expected_revision: int,
    ) -> LearningCandidate:
        current = self.get_candidate(candidate.learning_candidate_id)
        if current.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate changed after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.revision,
                },
            )
        if candidate.revision != expected_revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate revision must increase exactly by one",
            )
        if candidate.created_at != current.created_at:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "learning candidate created_at is immutable",
            )
        key = (candidate.learning_candidate_id, candidate.revision)
        if key in self._candidates:
            raise ContractError(ErrorCode.CONFLICT, "learning candidate revision already exists")
        self._candidates[key] = candidate
        return candidate

    def get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate:
        if revision is None:
            revisions = [
                current_revision
                for current_id, current_revision in self._candidates
                if current_id == learning_candidate_id
            ]
            if not revisions:
                raise ContractError(ErrorCode.NOT_FOUND, "learning candidate was not found")
            revision = max(revisions)
        try:
            return self._candidates[(learning_candidate_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "learning candidate revision was not found",
            ) from exc

    def list_candidates(self) -> tuple[LearningCandidate, ...]:
        ids = sorted({candidate_id for candidate_id, _ in self._candidates})
        return tuple(self.get_candidate(candidate_id) for candidate_id in ids)

    def find_candidate_by_dedupe_key(self, dedupe_key: str) -> LearningCandidate | None:
        candidate_id = self._candidate_keys.get(dedupe_key)
        return None if candidate_id is None else self.get_candidate(candidate_id)


class SQLiteLearningRepository:
    """Restart-safe SQLite repository with append-only candidate revision rows."""

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

                CREATE TABLE IF NOT EXISTS learning_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    dedupe_key TEXT NOT NULL UNIQUE,
                    digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning_candidate_revisions (
                    learning_candidate_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (learning_candidate_id, revision)
                );

                CREATE TABLE IF NOT EXISTS learning_candidate_keys (
                    dedupe_key TEXT PRIMARY KEY,
                    learning_candidate_id TEXT NOT NULL UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_learning_candidate_updated
                    ON learning_candidate_revisions(updated_at);
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            self._rebuild_candidate_dedupe_keys(connection)

    def _rebuild_candidate_dedupe_keys(self, connection: sqlite3.Connection) -> None:
        """Migrate persisted key indexes to the current canonical dedupe algorithm."""

        rows = connection.execute(
            """
            SELECT revisions.payload_json
            FROM learning_candidate_revisions AS revisions
            JOIN (
                SELECT learning_candidate_id, MAX(revision) AS revision
                FROM learning_candidate_revisions
                GROUP BY learning_candidate_id
            ) AS current
            ON current.learning_candidate_id = revisions.learning_candidate_id
            AND current.revision = revisions.revision
            """
        ).fetchall()
        expected: dict[str, str] = {}
        for row in rows:
            candidate = candidate_from_dict(_load(str(row["payload_json"])))
            existing_id = expected.get(candidate.dedupe_key)
            if existing_id is not None and existing_id != candidate.learning_candidate_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "stored learning candidates collide under the canonical dedupe key",
                    details={
                        "learning_candidate_id": candidate.learning_candidate_id,
                        "conflicting_learning_candidate_id": existing_id,
                    },
                )
            expected[candidate.dedupe_key] = candidate.learning_candidate_id

        current_rows = connection.execute(
            "SELECT dedupe_key, learning_candidate_id FROM learning_candidate_keys"
        ).fetchall()
        current = {
            str(row["dedupe_key"]): str(row["learning_candidate_id"])
            for row in current_rows
        }
        if current == expected:
            return

        connection.execute("DELETE FROM learning_candidate_keys")
        connection.executemany(
            "INSERT INTO learning_candidate_keys(dedupe_key, learning_candidate_id) VALUES (?, ?)",
            sorted(expected.items()),
        )

    def create_feedback(
        self,
        feedback: FeedbackRecord,
        *,
        dedupe_key: str,
    ) -> tuple[FeedbackRecord, bool]:
        _require_key(dedupe_key, "feedback dedupe_key")
        payload = _dump(feedback_to_dict(feedback))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM learning_feedback WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                return feedback_from_dict(_load(str(existing["payload_json"]))), False
            by_id = connection.execute(
                "SELECT payload_json FROM learning_feedback WHERE feedback_id = ?",
                (feedback.feedback_id,),
            ).fetchone()
            if by_id is not None:
                current = feedback_from_dict(_load(str(by_id["payload_json"])))
                if current.content_digest != feedback.content_digest:
                    raise ContractError(ErrorCode.CONFLICT, "feedback ID already exists")
                return current, False
            connection.execute(
                "INSERT INTO learning_feedback"
                "(feedback_id, dedupe_key, digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    feedback.feedback_id,
                    dedupe_key,
                    feedback.content_digest,
                    payload,
                    feedback.created_at.isoformat(),
                ),
            )
        return feedback, True

    def get_feedback(self, feedback_id: str) -> FeedbackRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM learning_feedback WHERE feedback_id = ?",
                (feedback_id,),
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "feedback was not found")
        return feedback_from_dict(_load(str(row["payload_json"])))

    def list_feedback(self) -> tuple[FeedbackRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM learning_feedback ORDER BY created_at, feedback_id"
            ).fetchall()
        return tuple(feedback_from_dict(_load(str(row["payload_json"]))) for row in rows)

    def create_candidate(
        self,
        candidate: LearningCandidate,
        *,
        dedupe_key: str,
    ) -> tuple[LearningCandidate, bool]:
        _require_key(dedupe_key, "candidate dedupe_key")
        if candidate.revision != 1:
            raise ContractError(
                ErrorCode.CONFLICT, "new learning candidate must start at revision 1"
            )
        payload = _dump(candidate_to_dict(candidate))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT learning_candidate_id FROM learning_candidate_keys WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                return self._get_candidate_with_connection(
                    connection,
                    str(existing["learning_candidate_id"]),
                    None,
                ), False
            try:
                connection.execute(
                    "INSERT INTO learning_candidate_revisions"
                    "(learning_candidate_id, revision, digest, payload_json, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate.learning_candidate_id,
                        candidate.revision,
                        candidate.content_digest,
                        payload,
                        candidate.created_at.isoformat(),
                        candidate.updated_at.isoformat(),
                    ),
                )
                connection.execute(
                    "INSERT INTO learning_candidate_keys(dedupe_key, learning_candidate_id) "
                    "VALUES (?, ?)",
                    (dedupe_key, candidate.learning_candidate_id),
                )
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate identity or dedupe key already exists",
                ) from exc
        return candidate, True

    def append_candidate(
        self,
        candidate: LearningCandidate,
        *,
        expected_revision: int,
    ) -> LearningCandidate:
        payload = _dump(candidate_to_dict(candidate))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = self._get_candidate_with_connection(
                connection,
                candidate.learning_candidate_id,
                None,
            )
            if current.revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate changed after the caller's base revision",
                    details={
                        "expected_revision": expected_revision,
                        "current_revision": current.revision,
                    },
                )
            if candidate.revision != expected_revision + 1:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate revision must increase exactly by one",
                )
            if candidate.created_at != current.created_at:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "learning candidate created_at is immutable",
                )
            try:
                connection.execute(
                    "INSERT INTO learning_candidate_revisions"
                    "(learning_candidate_id, revision, digest, payload_json, "
                    "created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        candidate.learning_candidate_id,
                        candidate.revision,
                        candidate.content_digest,
                        payload,
                        candidate.created_at.isoformat(),
                        candidate.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate revision already exists",
                ) from exc
        return candidate

    def get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate:
        with self._connect() as connection:
            return self._get_candidate_with_connection(
                connection,
                learning_candidate_id,
                revision,
            )

    def _get_candidate_with_connection(
        self,
        connection: sqlite3.Connection,
        learning_candidate_id: str,
        revision: int | None,
    ) -> LearningCandidate:
        if revision is None:
            row = connection.execute(
                "SELECT payload_json FROM learning_candidate_revisions "
                "WHERE learning_candidate_id = ? ORDER BY revision DESC LIMIT 1",
                (learning_candidate_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT payload_json FROM learning_candidate_revisions "
                "WHERE learning_candidate_id = ? AND revision = ?",
                (learning_candidate_id, revision),
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "learning candidate was not found")
        return candidate_from_dict(_load(str(row["payload_json"])))

    def list_candidates(self) -> tuple[LearningCandidate, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revisions.payload_json
                FROM learning_candidate_revisions AS revisions
                JOIN (
                    SELECT learning_candidate_id, MAX(revision) AS revision
                    FROM learning_candidate_revisions
                    GROUP BY learning_candidate_id
                ) AS current
                ON current.learning_candidate_id = revisions.learning_candidate_id
                AND current.revision = revisions.revision
                ORDER BY revisions.updated_at, revisions.learning_candidate_id
                """
            ).fetchall()
        return tuple(candidate_from_dict(_load(str(row["payload_json"]))) for row in rows)

    def find_candidate_by_dedupe_key(self, dedupe_key: str) -> LearningCandidate | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT learning_candidate_id FROM learning_candidate_keys WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if row is None:
                return None
            return self._get_candidate_with_connection(
                connection,
                str(row["learning_candidate_id"]),
                None,
            )


def _require_key(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _dump(payload: dict[str, JsonValue]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _load(payload: str) -> dict[str, Any]:
    loaded = json.loads(payload)
    if not isinstance(loaded, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "learning payload must be an object")
    return cast(dict[str, Any], loaded)
