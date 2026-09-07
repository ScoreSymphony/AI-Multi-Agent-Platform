"""Durable Decision Record repository and SQLite reference implementation (#598)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    DecisionAlternative,
    DecisionAlternativeStatus,
    DecisionOutcome,
    DecisionRecord,
    DecisionReference,
    DecisionStatus,
)


@runtime_checkable
class DecisionRepository(Protocol):
    def create(self, record: DecisionRecord) -> DecisionRecord: ...
    def create_superseding(self, previous_id: str, record: DecisionRecord) -> DecisionRecord: ...
    def get(self, decision_record_id: str) -> DecisionRecord: ...
    def list(self) -> tuple[DecisionRecord, ...]: ...
    def superseded_by(self, decision_record_id: str) -> str | None: ...
    def withdraw(self, decision_record_id: str, *, actor_ref: str, reason: str) -> None: ...
    def withdrawal(self, decision_record_id: str) -> tuple[datetime, str] | None: ...
    def add_downstream_ref(self, decision_record_id: str, reference: DecisionReference) -> None: ...
    def downstream_refs(self, decision_record_id: str) -> tuple[DecisionReference, ...]: ...


class SqliteDecisionRepository(DecisionRepository):
    """Append-only decision persistence; record payload rows are never updated."""

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
                CREATE TABLE IF NOT EXISTS decision_records (
                    decision_record_id TEXT PRIMARY KEY,
                    digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_supersessions (
                    predecessor_id TEXT PRIMARY KEY,
                    successor_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_withdrawals (
                    decision_record_id TEXT PRIMARY KEY,
                    actor_ref TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    withdrawn_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_downstream_refs (
                    decision_record_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (decision_record_id, ordinal)
                );
                """
            )

    def create(self, record: DecisionRecord) -> DecisionRecord:
        if record.supersedes is not None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "superseding DecisionRecord must use create_superseding",
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._insert_record(connection, record)
        return record

    def create_superseding(self, previous_id: str, record: DecisionRecord) -> DecisionRecord:
        validate_id(previous_id, "decision_record")
        if record.supersedes != previous_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement DecisionRecord must reference the exact superseded record",
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT payload_json FROM decision_records WHERE decision_record_id = ?",
                (previous_id,),
            ).fetchone()
            if previous is None:
                raise ContractError(ErrorCode.NOT_FOUND, "superseded DecisionRecord was not found")
            if connection.execute(
                "SELECT 1 FROM decision_supersessions WHERE predecessor_id = ?",
                (previous_id,),
            ).fetchone() is not None:
                raise ContractError(ErrorCode.CONFLICT, "DecisionRecord is already superseded")
            if connection.execute(
                "SELECT 1 FROM decision_withdrawals WHERE decision_record_id = ?",
                (previous_id,),
            ).fetchone() is not None:
                raise ContractError(ErrorCode.CONFLICT, "withdrawn DecisionRecord cannot be superseded")
            self._insert_record(connection, record)
            connection.execute(
                "INSERT INTO decision_supersessions(predecessor_id, successor_id, created_at) "
                "VALUES (?, ?, ?)",
                (previous_id, record.id, record.created_at.isoformat()),
            )
        return record

    def get(self, decision_record_id: str) -> DecisionRecord:
        validate_id(decision_record_id, "decision_record")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM decision_records WHERE decision_record_id = ?",
                (decision_record_id,),
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "DecisionRecord was not found")
        return _record_from_json(_load(str(row["payload_json"])))

    def list(self) -> tuple[DecisionRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM decision_records ORDER BY created_at, decision_record_id"
            ).fetchall()
        return tuple(_record_from_json(_load(str(row["payload_json"]))) for row in rows)

    def superseded_by(self, decision_record_id: str) -> str | None:
        validate_id(decision_record_id, "decision_record")
        self.get(decision_record_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT successor_id FROM decision_supersessions WHERE predecessor_id = ?",
                (decision_record_id,),
            ).fetchone()
        return None if row is None else str(row["successor_id"])

    def withdraw(self, decision_record_id: str, *, actor_ref: str, reason: str) -> None:
        validate_id(decision_record_id, "decision_record")
        if not actor_ref.strip() or not reason.strip():
            raise ValueError("decision withdrawal actor_ref and reason must not be blank")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM decision_records WHERE decision_record_id = ?",
                (decision_record_id,),
            ).fetchone() is None:
                raise ContractError(ErrorCode.NOT_FOUND, "DecisionRecord was not found")
            if connection.execute(
                "SELECT 1 FROM decision_supersessions WHERE predecessor_id = ?",
                (decision_record_id,),
            ).fetchone() is not None:
                raise ContractError(ErrorCode.CONFLICT, "superseded DecisionRecord cannot be withdrawn")
            try:
                connection.execute(
                    "INSERT INTO decision_withdrawals"
                    "(decision_record_id, actor_ref, reason, withdrawn_at) VALUES (?, ?, ?, ?)",
                    (decision_record_id, actor_ref, reason, datetime.now().astimezone().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ContractError(ErrorCode.CONFLICT, "DecisionRecord is already withdrawn") from exc

    def withdrawal(self, decision_record_id: str) -> tuple[datetime, str] | None:
        validate_id(decision_record_id, "decision_record")
        self.get(decision_record_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT withdrawn_at, reason FROM decision_withdrawals WHERE decision_record_id = ?",
                (decision_record_id,),
            ).fetchone()
        if row is None:
            return None
        return datetime.fromisoformat(str(row["withdrawn_at"])), str(row["reason"])

    def add_downstream_ref(self, decision_record_id: str, reference: DecisionReference) -> None:
        validate_id(decision_record_id, "decision_record")
        payload = _dump(_reference_to_json(reference))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM decision_records WHERE decision_record_id = ?",
                (decision_record_id,),
            ).fetchone() is None:
                raise ContractError(ErrorCode.NOT_FOUND, "DecisionRecord was not found")
            existing = connection.execute(
                "SELECT payload_json FROM decision_downstream_refs WHERE decision_record_id = ?",
                (decision_record_id,),
            ).fetchall()
            if any(str(row["payload_json"]) == payload for row in existing):
                return
            ordinal = len(existing)
            connection.execute(
                "INSERT INTO decision_downstream_refs(decision_record_id, ordinal, payload_json) "
                "VALUES (?, ?, ?)",
                (decision_record_id, ordinal, payload),
            )

    def downstream_refs(self, decision_record_id: str) -> tuple[DecisionReference, ...]:
        validate_id(decision_record_id, "decision_record")
        self.get(decision_record_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM decision_downstream_refs "
                "WHERE decision_record_id = ? ORDER BY ordinal",
                (decision_record_id,),
            ).fetchall()
        return tuple(_reference_from_json(_load(str(row["payload_json"]))) for row in rows)

    @staticmethod
    def _insert_record(connection: sqlite3.Connection, record: DecisionRecord) -> None:
        payload = _dump(_record_to_json(record))
        try:
            connection.execute(
                "INSERT INTO decision_records(decision_record_id, digest, payload_json, created_at) "
                "VALUES (?, ?, ?, ?)",
                (record.id, record.content_digest, payload, record.created_at.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            raise ContractError(ErrorCode.CONFLICT, "DecisionRecord already exists") from exc


def _record_to_json(record: DecisionRecord) -> dict[str, JsonValue]:
    return {
        "id": record.id,
        "title": record.title,
        "subject": record.subject,
        "category": record.category,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "question": record.question,
        "alternatives": [_alternative_to_json(item) for item in record.alternatives],
        "outcome": record.outcome.value,
        "rationale": record.rationale,
        "actor_ref": record.actor_ref,
        "subject_ref": _optional_reference(record.subject_ref),
        "evidence_refs": [_reference_to_json(item) for item in record.evidence_refs],
        "evaluation_refs": [_reference_to_json(item) for item in record.evaluation_refs],
        "finding_refs": [_reference_to_json(item) for item in record.finding_refs],
        "cost_resource_refs": [_reference_to_json(item) for item in record.cost_resource_refs],
        "reviewer_refs": list(record.reviewer_refs),
        "approval_ref": _optional_reference(record.approval_ref),
        "adr_ref": _optional_reference(record.adr_ref),
        "effective_at": record.effective_at.isoformat(),
        "review_at": record.review_at.isoformat() if record.review_at is not None else None,
        "review_condition": record.review_condition,
        "status": record.status.value,
        "supersedes": record.supersedes,
        "revision": record.revision,
        "created_at": record.created_at.isoformat(),
        "content_digest": record.content_digest,
        "schema_version": record.schema_version,
    }


def _record_from_json(value: dict[str, JsonValue]) -> DecisionRecord:
    return DecisionRecord(
        id=_string(value, "id"),
        title=_string(value, "title"),
        subject=_string(value, "subject"),
        category=_string(value, "category"),
        scope_type=_string(value, "scope_type"),
        scope_id=_optional_string(value.get("scope_id")),
        question=_string(value, "question"),
        alternatives=tuple(
            _alternative_from_json(_object(item)) for item in _list(value, "alternatives")
        ),
        outcome=DecisionOutcome(_string(value, "outcome")),
        rationale=_string(value, "rationale"),
        actor_ref=_string(value, "actor_ref"),
        subject_ref=_optional_reference_from_json(value.get("subject_ref")),
        evidence_refs=_reference_tuple(value, "evidence_refs"),
        evaluation_refs=_reference_tuple(value, "evaluation_refs"),
        finding_refs=_reference_tuple(value, "finding_refs"),
        cost_resource_refs=_reference_tuple(value, "cost_resource_refs"),
        reviewer_refs=tuple(_string_item(item) for item in _list(value, "reviewer_refs")),
        approval_ref=_optional_reference_from_json(value.get("approval_ref")),
        adr_ref=_optional_reference_from_json(value.get("adr_ref")),
        effective_at=datetime.fromisoformat(_string(value, "effective_at")),
        review_at=_optional_datetime(value.get("review_at")),
        review_condition=_optional_string(value.get("review_condition")),
        status=DecisionStatus(_string(value, "status")),
        supersedes=_optional_string(value.get("supersedes")),
        revision=_integer(value, "revision"),
        created_at=datetime.fromisoformat(_string(value, "created_at")),
        content_digest=_string(value, "content_digest"),
        schema_version=_string(value, "schema_version"),
    )


def _alternative_to_json(value: DecisionAlternative) -> dict[str, JsonValue]:
    return {
        "label": value.label,
        "status": value.status.value,
        "resource_ref": _optional_reference(value.resource_ref),
        "evidence_refs": [_reference_to_json(item) for item in value.evidence_refs],
        "trade_offs": list(value.trade_offs),
        "unknowns": list(value.unknowns),
    }


def _alternative_from_json(value: dict[str, JsonValue]) -> DecisionAlternative:
    return DecisionAlternative(
        label=_string(value, "label"),
        status=DecisionAlternativeStatus(_string(value, "status")),
        resource_ref=_optional_reference_from_json(value.get("resource_ref")),
        evidence_refs=_reference_tuple(value, "evidence_refs"),
        trade_offs=tuple(_string_item(item) for item in _list(value, "trade_offs")),
        unknowns=tuple(_string_item(item) for item in _list(value, "unknowns")),
    )


def _reference_to_json(value: DecisionReference) -> dict[str, JsonValue]:
    return {
        "kind": value.kind,
        "resource_id": value.resource_id,
        "revision": value.revision,
        "digest": value.digest,
        "locator": value.locator,
        "metadata": dict(value.metadata),
    }


def _reference_from_json(value: dict[str, JsonValue]) -> DecisionReference:
    revision = value.get("revision")
    if revision is not None and not isinstance(revision, str | int):
        raise ValueError("decision reference revision must be a string or integer")
    metadata_value = value.get("metadata", {})
    metadata = _object(metadata_value)
    return DecisionReference(
        kind=_string(value, "kind"),
        resource_id=_string(value, "resource_id"),
        revision=revision,
        digest=_optional_string(value.get("digest")),
        locator=_optional_string(value.get("locator")),
        metadata=metadata,
    )


def _optional_reference(value: DecisionReference | None) -> dict[str, JsonValue] | None:
    return None if value is None else _reference_to_json(value)


def _optional_reference_from_json(value: JsonValue) -> DecisionReference | None:
    return None if value is None else _reference_from_json(_object(value))


def _reference_tuple(value: dict[str, JsonValue], key: str) -> tuple[DecisionReference, ...]:
    return tuple(_reference_from_json(_object(item)) for item in _list(value, key))


def _dump(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _load(value: str) -> dict[str, JsonValue]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("stored decision payload must be an object")
    return cast(dict[str, JsonValue], loaded)


def _object(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError("decision payload value must be an object")
    return cast(dict[str, JsonValue], value)


def _list(value: dict[str, JsonValue], key: str) -> list[JsonValue]:
    item = value.get(key)
    if not isinstance(item, list):
        raise ValueError(f"decision payload {key} must be an array")
    return item


def _string(value: dict[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"decision payload {key} must be a string")
    return item


def _string_item(value: JsonValue) -> str:
    if not isinstance(value, str):
        raise ValueError("decision payload array item must be a string")
    return value


def _integer(value: dict[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"decision payload {key} must be an integer")
    return item


def _optional_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional decision payload value must be a string")
    return value


def _optional_datetime(value: JsonValue) -> datetime | None:
    text = _optional_string(value)
    return None if text is None else datetime.fromisoformat(text)
