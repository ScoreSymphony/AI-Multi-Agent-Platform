"""Durable stdlib-only SQLite persistence for evaluation history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .codec import (
    decode_comparison,
    decode_result,
    decode_run,
    encode_comparison,
    encode_result,
    encode_run,
)
from .models import ComparisonReport, EvaluationResult, EvaluationRun

_STORAGE_SCHEMA_VERSION = "1"


def _require_limit(limit: int) -> int:
    if limit <= 0:
        raise ValueError("evaluation history limit must be greater than zero")
    return limit


class SqliteEvaluationRepository:
    """Restart-safe evaluation run/result/comparison history and indexed case queries."""

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
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_storage_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_runs (
                        run_id TEXT PRIMARY KEY,
                        suite_id TEXT NOT NULL,
                        suite_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        completed_at TEXT,
                        run_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_results (
                        result_id TEXT PRIMARY KEY,
                        evaluation_run_id TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        case_version TEXT NOT NULL,
                        evaluator_id TEXT NOT NULL,
                        outcome TEXT NOT NULL,
                        repetition_index INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        result_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS evaluation_comparisons (
                        current_run_id TEXT PRIMARY KEY,
                        baseline_run_id TEXT NOT NULL,
                        policy_id TEXT NOT NULL,
                        policy_version TEXT NOT NULL,
                        comparison_json TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evaluation_runs_suite_started "
                    "ON evaluation_runs(suite_id, suite_version, started_at DESC)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evaluation_results_run_created "
                    "ON evaluation_results(evaluation_run_id, created_at, result_id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evaluation_results_case_evaluator_created "
                    "ON evaluation_results(case_id, evaluator_id, created_at DESC)"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO evaluation_storage_meta(key, value) VALUES (?, ?)",
                    ("schema_version", _STORAGE_SCHEMA_VERSION),
                )
                row = connection.execute(
                    "SELECT value FROM evaluation_storage_meta WHERE key = 'schema_version'"
                ).fetchone()
                if row is None or str(row["value"]) != _STORAGE_SCHEMA_VERSION:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "unsupported evaluation SQLite storage schema version",
                    )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize evaluation SQLite storage",
            ) from exc

    @staticmethod
    def _encode_run(run: EvaluationRun) -> str:
        try:
            return encode_run(run)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "evaluation run is not strict-JSON serializable",
            ) from exc

    @staticmethod
    def _encode_result(result: EvaluationResult) -> str:
        try:
            return encode_result(result)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "evaluation result is not strict-JSON serializable",
            ) from exc

    @staticmethod
    def _encode_comparison(comparison: ComparisonReport) -> str:
        try:
            return encode_comparison(comparison)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "evaluation comparison is not strict-JSON serializable",
            ) from exc

    @staticmethod
    def _decode_run(raw: str) -> EvaluationRun:
        try:
            return decode_run(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored evaluation run is invalid",
            ) from exc

    @staticmethod
    def _decode_result(raw: str) -> EvaluationResult:
        try:
            return decode_result(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored evaluation result is invalid",
            ) from exc

    @staticmethod
    def _decode_comparison(raw: str) -> ComparisonReport:
        try:
            return decode_comparison(raw)
        except (TypeError, ValueError, KeyError) as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored evaluation comparison is invalid",
            ) from exc

    def save_run(self, run: EvaluationRun) -> None:
        raw = self._encode_run(run)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_runs(
                        run_id, suite_id, suite_version, status, started_at, completed_at, run_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        suite_id = excluded.suite_id,
                        suite_version = excluded.suite_version,
                        status = excluded.status,
                        started_at = excluded.started_at,
                        completed_at = excluded.completed_at,
                        run_json = excluded.run_json
                    """,
                    (
                        run.run_id,
                        run.suite_id,
                        run.suite_version,
                        run.status.value,
                        run.started_at.isoformat(),
                        None if run.completed_at is None else run.completed_at.isoformat(),
                        raw,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to persist evaluation run"
            ) from exc

    def get_run(self, run_id: str) -> EvaluationRun | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT run_json FROM evaluation_runs WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read evaluation run") from exc
        return None if row is None else self._decode_run(str(row["run_json"]))

    def list_runs(
        self,
        *,
        suite_id: str | None = None,
        suite_version: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationRun, ...]:
        _require_limit(limit)
        clauses: list[str] = []
        parameters: list[str | int] = []
        if suite_id is not None:
            clauses.append("suite_id = ?")
            parameters.append(suite_id)
        if suite_version is not None:
            clauses.append("suite_version = ?")
            parameters.append(suite_version)
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        parameters.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT run_json FROM evaluation_runs"
                    + where
                    + " ORDER BY started_at DESC, run_id DESC LIMIT ?",
                    tuple(parameters),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to list evaluation runs") from exc
        return tuple(self._decode_run(str(row["run_json"])) for row in rows)

    def _require_run(self, run_id: str) -> None:
        if self.get_run(run_id) is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"evaluation run not found: {run_id}")

    def save_result(self, result: EvaluationResult) -> None:
        self._require_run(result.evaluation_run_id)
        raw = self._encode_result(result)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_results(
                        result_id, evaluation_run_id, case_id, case_version, evaluator_id,
                        outcome, repetition_index, created_at, result_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(result_id) DO UPDATE SET
                        evaluation_run_id = excluded.evaluation_run_id,
                        case_id = excluded.case_id,
                        case_version = excluded.case_version,
                        evaluator_id = excluded.evaluator_id,
                        outcome = excluded.outcome,
                        repetition_index = excluded.repetition_index,
                        created_at = excluded.created_at,
                        result_json = excluded.result_json
                    """,
                    (
                        result.result_id,
                        result.evaluation_run_id,
                        result.case_id,
                        result.case_version,
                        result.evaluator.evaluator_id,
                        result.outcome.value,
                        result.repetition_index,
                        result.created_at.isoformat(),
                        raw,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to persist evaluation result"
            ) from exc

    def list_results(self, evaluation_run_id: str) -> tuple[EvaluationResult, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT result_json FROM evaluation_results
                    WHERE evaluation_run_id = ?
                    ORDER BY created_at ASC, result_id ASC
                    """,
                    (evaluation_run_id,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to list evaluation results"
            ) from exc
        return tuple(self._decode_result(str(row["result_json"])) for row in rows)

    def list_case_results(
        self,
        *,
        case_id: str,
        evaluator_id: str | None = None,
        limit: int = 100,
    ) -> tuple[EvaluationResult, ...]:
        if not case_id.strip():
            raise ValueError("evaluation history case_id must not be blank")
        _require_limit(limit)
        parameters: list[str | int] = [case_id]
        evaluator_clause = ""
        if evaluator_id is not None:
            evaluator_clause = " AND evaluator_id = ?"
            parameters.append(evaluator_id)
        parameters.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT result_json FROM evaluation_results "
                    "WHERE case_id = ?"
                    + evaluator_clause
                    + " ORDER BY created_at DESC, result_id DESC LIMIT ?",
                    tuple(parameters),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to list evaluation case history",
            ) from exc
        return tuple(self._decode_result(str(row["result_json"])) for row in rows)

    def save_comparison(self, comparison: ComparisonReport) -> None:
        self._require_run(comparison.current_run_id)
        self._require_run(comparison.baseline_run_id)
        raw = self._encode_comparison(comparison)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_comparisons(
                        current_run_id, baseline_run_id, policy_id, policy_version, comparison_json
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(current_run_id) DO UPDATE SET
                        baseline_run_id = excluded.baseline_run_id,
                        policy_id = excluded.policy_id,
                        policy_version = excluded.policy_version,
                        comparison_json = excluded.comparison_json
                    """,
                    (
                        comparison.current_run_id,
                        comparison.baseline_run_id,
                        comparison.policy_id,
                        comparison.policy_version,
                        raw,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist evaluation comparison",
            ) from exc

    def get_comparison(self, current_run_id: str) -> ComparisonReport | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT comparison_json FROM evaluation_comparisons WHERE current_run_id = ?",
                    (current_run_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read evaluation comparison",
            ) from exc
        return None if row is None else self._decode_comparison(str(row["comparison_json"]))
