"""Evaluation-owned persistence seam for mutable versioned Suite assets.

Configured/built-in suites may remain immutable deployment inputs. Suites created through
northbound mutation or portability are stored here, in the same durable Evaluation SQLite
database, without giving portability ownership of Evaluation configuration.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from math import isfinite
from pathlib import Path
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .config import parse_evaluation_suite
from .models import EvaluationCase, EvaluationSuite


class EvaluationSuiteAssetRepository(Protocol):
    """Create/read/delete boundary for exact immutable EvaluationSuite versions."""

    def create_suite(self, suite: EvaluationSuite) -> str: ...

    def get_suite(self, suite_ref: str) -> EvaluationSuite | None: ...

    def list_suites(self) -> tuple[EvaluationSuite, ...]: ...

    def delete_suite(self, suite_ref: str, *, expected_checksum: str | None = None) -> None: ...


def suite_ref(suite: EvaluationSuite) -> str:
    return f"{suite.suite_id}@{suite.version}"


def suite_payload(suite: EvaluationSuite) -> dict[str, JsonValue]:
    """Project a canonical Suite to the strict JSON shape accepted by config parsing."""

    return {
        "suite_id": suite.suite_id,
        "name": suite.name,
        "version": suite.version,
        "description": suite.description,
        "tags": list(suite.tags),
        "cases": [_case_payload(case) for case in suite.cases],
    }


def suite_checksum(suite: EvaluationSuite) -> str:
    raw = _encode_suite(suite)
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _case_payload(case: EvaluationCase) -> dict[str, JsonValue]:
    return {
        "case_id": case.case_id,
        "name": case.name,
        "version": case.version,
        "input_template": _json_value(case.input_template),
        "fixtures": list(case.fixtures),
        "assertions": [
            {
                "assertion_id": item.assertion_id,
                "path": item.path,
                "operator": item.operator.value,
                "expected": _json_value(item.expected),
                "message": item.message,
            }
            for item in case.assertions
        ],
        "metric_rules": [
            {
                "rule_id": item.rule_id,
                "metric_name": item.metric_name,
                "operator": item.operator.value,
                "threshold": item.threshold,
                "unit": item.unit,
            }
            for item in case.metric_rules
        ],
        "rubric": [
            {
                "criterion_id": item.criterion_id,
                "description": item.description,
                "weight": item.weight,
                "minimum_score": item.minimum_score,
            }
            for item in case.rubric
        ],
        "timeout_seconds": case.timeout_seconds,
        "resource_limits": [
            {"key": item.key, "value": item.value} for item in case.resource_limits
        ],
        "tags": list(case.tags),
        "category": case.category,
        "difficulty": case.difficulty,
    }


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("evaluation suite contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("evaluation suite JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported EvaluationSuite JSON value: {type(value).__name__}")


def _encode_suite(suite: EvaluationSuite) -> str:
    return json.dumps(
        suite_payload(suite),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _decode_suite(raw: str) -> EvaluationSuite:
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("stored evaluation suite root must be an object")
        return parse_evaluation_suite(value)
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "stored evaluation suite asset is invalid",
        ) from exc


class SqliteEvaluationSuiteAssetRepository:
    """Restart-safe create-only Suite asset storage in the Evaluation database."""

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
                    CREATE TABLE IF NOT EXISTS evaluation_suite_assets (
                        suite_ref TEXT PRIMARY KEY,
                        suite_id TEXT NOT NULL,
                        suite_version TEXT NOT NULL,
                        checksum TEXT NOT NULL,
                        suite_json TEXT NOT NULL,
                        UNIQUE(suite_id, suite_version)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to initialize evaluation suite asset storage",
            ) from exc

    def create_suite(self, suite: EvaluationSuite) -> str:
        reference = suite_ref(suite)
        raw = _encode_suite(suite)
        checksum = f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO evaluation_suite_assets(
                        suite_ref, suite_id, suite_version, checksum, suite_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (reference, suite.suite_id, suite.version, checksum, raw),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"evaluation suite version already exists: {reference}",
                details={"suite_ref": reference},
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to persist evaluation suite asset",
            ) from exc
        return checksum

    def get_suite(self, suite_ref_value: str) -> EvaluationSuite | None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT suite_ref, checksum, suite_json
                    FROM evaluation_suite_assets
                    WHERE suite_ref = ?
                    """,
                    (suite_ref_value,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to read evaluation suite asset",
            ) from exc
        if row is None:
            return None
        return self._validated_row(row)

    def list_suites(self) -> tuple[EvaluationSuite, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT suite_ref, checksum, suite_json
                    FROM evaluation_suite_assets
                    ORDER BY suite_id ASC, suite_version ASC
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to list evaluation suite assets",
            ) from exc
        return tuple(self._validated_row(row) for row in rows)

    def delete_suite(self, suite_ref_value: str, *, expected_checksum: str | None = None) -> None:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT suite_ref, suite_id, suite_version, checksum, suite_json
                    FROM evaluation_suite_assets
                    WHERE suite_ref = ?
                    """,
                    (suite_ref_value,),
                ).fetchone()
                if row is None:
                    raise ContractError(
                        ErrorCode.NOT_FOUND,
                        f"evaluation suite asset not found: {suite_ref_value}",
                    )
                checksum = str(row["checksum"])
                if expected_checksum is not None and checksum != expected_checksum:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "evaluation suite asset changed before compensation",
                        details={"suite_ref": suite_ref_value},
                    )
                dependent = connection.execute(
                    """
                    SELECT run_id
                    FROM evaluation_runs
                    WHERE suite_id = ? AND suite_version = ?
                    LIMIT 1
                    """,
                    (str(row["suite_id"]), str(row["suite_version"])),
                ).fetchone()
                if dependent is not None:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "evaluation suite asset is referenced by durable run history",
                        details={
                            "suite_ref": suite_ref_value,
                            "run_id": str(dependent["run_id"]),
                        },
                    )
                connection.execute(
                    "DELETE FROM evaluation_suite_assets WHERE suite_ref = ?",
                    (suite_ref_value,),
                )
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to delete evaluation suite asset",
            ) from exc

    @staticmethod
    def _validated_row(row: sqlite3.Row) -> EvaluationSuite:
        raw = str(row["suite_json"])
        checksum = f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
        if checksum != str(row["checksum"]):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored evaluation suite checksum mismatch",
            )
        suite = _decode_suite(raw)
        if suite_ref(suite) != str(row["suite_ref"]):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored evaluation suite identity disagrees with its index",
            )
        return suite


__all__ = [
    "EvaluationSuiteAssetRepository",
    "SqliteEvaluationSuiteAssetRepository",
    "suite_checksum",
    "suite_payload",
    "suite_ref",
]
