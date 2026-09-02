"""Durable stdlib SQLite baseline for the canonical kernel repository."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import Event, ExternalRef, OwnerRef, Provenance

from .repository import CommandRecord, CommitResult, EventRepository


class SqliteKernelRepository(EventRepository):
    """Transactional event/idempotency store used for restart and recovery flows."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kernel_events (
                    stream_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_json TEXT NOT NULL,
                    PRIMARY KEY (stream_id, sequence)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS kernel_commands (
                    scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    result_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    PRIMARY KEY (scope, idempotency_key)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_kernel_events_stream "
                "ON kernel_events(stream_id, sequence)"
            )

    async def read_events(self, stream_id: str) -> tuple[PlatformEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event_json FROM kernel_events WHERE stream_id = ? ORDER BY sequence ASC",
                (stream_id,),
            ).fetchall()
        return tuple(self._decode_event(str(row["event_json"])) for row in rows)

    async def revision(self, stream_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS revision FROM kernel_events WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
        return 0 if row is None else int(row["revision"])

    async def find_command(self, scope: str, idempotency_key: str) -> CommandRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT scope, idempotency_key, operation, stream_id, result_id, event_id
                FROM kernel_commands WHERE scope = ? AND idempotency_key = ?
                """,
                (scope, idempotency_key),
            ).fetchone()
        return None if row is None else self._command_from_row(row)

    async def list_stream_ids(self) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT stream_id FROM kernel_events ORDER BY stream_id"
            ).fetchall()
        return tuple(str(row["stream_id"]) for row in rows)

    async def commit(
        self,
        *,
        stream_id: str,
        expected_revision: int,
        events: tuple[PlatformEvent, ...],
        command: CommandRecord | None = None,
    ) -> CommitResult:
        if expected_revision < 0:
            raise ValueError("expected_revision must be >= 0")
        if not events:
            raise ValueError("kernel commits must contain at least one event")

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if command is not None:
                existing_row = connection.execute(
                    """
                    SELECT scope, idempotency_key, operation, stream_id, result_id, event_id
                    FROM kernel_commands WHERE scope = ? AND idempotency_key = ?
                    """,
                    (command.scope, command.idempotency_key),
                ).fetchone()
                if existing_row is not None:
                    existing = self._command_from_row(existing_row)
                    revision_row = connection.execute(
                        "SELECT COUNT(*) AS revision FROM kernel_events WHERE stream_id = ?",
                        (existing.stream_id,),
                    ).fetchone()
                    connection.rollback()
                    return CommitResult(
                        applied=False,
                        revision=0 if revision_row is None else int(revision_row["revision"]),
                        command=existing,
                    )

            row = connection.execute(
                "SELECT COUNT(*) AS revision FROM kernel_events WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
            actual_revision = 0 if row is None else int(row["revision"])
            if actual_revision != expected_revision:
                connection.rollback()
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"stale stream revision for {stream_id}: "
                    f"expected {expected_revision}, actual {actual_revision}",
                )

            for offset, event in enumerate(events, start=1):
                if event.correlation_id != stream_id:
                    connection.rollback()
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "event correlation_id must equal canonical stream id",
                    )
                connection.execute(
                    "INSERT INTO kernel_events(stream_id, sequence, event_id, event_json) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        stream_id,
                        expected_revision + offset,
                        event.id,
                        self._encode_event(event),
                    ),
                )

            if command is not None:
                connection.execute(
                    """
                    INSERT INTO kernel_commands(
                        scope, idempotency_key, operation, stream_id, result_id, event_id
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        command.scope,
                        command.idempotency_key,
                        command.operation,
                        command.stream_id,
                        command.result_id,
                        command.event_id,
                    ),
                )
            connection.commit()
            return CommitResult(
                applied=True,
                revision=expected_revision + len(events),
                command=command,
            )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise ContractError(ErrorCode.CONFLICT, f"kernel persistence conflict: {exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _command_from_row(row: sqlite3.Row) -> CommandRecord:
        return CommandRecord(
            scope=str(row["scope"]),
            idempotency_key=str(row["idempotency_key"]),
            operation=str(row["operation"]),
            stream_id=str(row["stream_id"]),
            result_id=str(row["result_id"]),
            event_id=str(row["event_id"]),
        )

    @staticmethod
    def _encode_event(event: PlatformEvent) -> str:
        payload: dict[str, object] = {
            "id": event.id,
            "event_type": event.event_type,
            "subject_type": event.subject_type,
            "subject_id": event.subject_id,
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "trace_id": event.trace_id,
            "occurred_at": event.occurred_at.isoformat(),
            "schema_version": event.schema_version,
            "project_id": event.project_id,
            "owner_ref": (
                None
                if event.owner_ref is None
                else {"type": event.owner_ref.type, "id": event.owner_ref.id}
            ),
            "payload": _jsonable(event.payload),
            "provenance": (
                None
                if event.provenance is None
                else {
                    "source": event.provenance.source,
                    "actor_ref": event.provenance.actor_ref,
                    "details": _jsonable(event.provenance.details),
                }
            ),
            "external_refs": [
                {"system": ref.system, "kind": ref.kind, "value": ref.value}
                for ref in event.external_refs
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_event(raw: str) -> PlatformEvent:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("stored event must be a JSON object")
        payload_obj = obj.get("payload")
        if not isinstance(payload_obj, dict):
            raise ValueError("stored event payload must be a JSON object")

        owner_obj = obj.get("owner_ref")
        owner_ref: OwnerRef | None = None
        if owner_obj is not None:
            if not isinstance(owner_obj, dict):
                raise ValueError("stored event owner_ref must be an object or null")
            owner_ref = OwnerRef(
                type=cast(Any, str(owner_obj["type"])),
                id=str(owner_obj["id"]),
            )

        provenance_obj = obj.get("provenance")
        provenance: Provenance | None = None
        if provenance_obj is not None:
            if not isinstance(provenance_obj, dict):
                raise ValueError("stored event provenance must be an object or null")
            details = provenance_obj.get("details", {})
            if not isinstance(details, dict):
                raise ValueError("stored provenance details must be an object")
            provenance = Provenance(
                source=str(provenance_obj["source"]),
                actor_ref=_nullable_string(provenance_obj.get("actor_ref")),
                details=details,
            )

        refs_obj = obj.get("external_refs", [])
        if not isinstance(refs_obj, list):
            raise ValueError("stored event external_refs must be an array")
        external_refs = tuple(
            ExternalRef(
                system=str(item["system"]),
                kind=str(item["kind"]),
                value=str(item["value"]),
            )
            for item in refs_obj
            if isinstance(item, dict)
        )
        if len(external_refs) != len(refs_obj):
            raise ValueError("stored event external_refs contains an invalid entry")

        return Event(
            id=str(obj["id"]),
            event_type=str(obj["event_type"]),
            subject_type=str(obj["subject_type"]),
            subject_id=str(obj["subject_id"]),
            correlation_id=str(obj["correlation_id"]),
            causation_id=_nullable_string(obj.get("causation_id")),
            trace_id=_nullable_string(obj.get("trace_id")),
            occurred_at=datetime.fromisoformat(str(obj["occurred_at"])),
            schema_version=str(obj["schema_version"]),
            project_id=_nullable_string(obj.get("project_id")),
            owner_ref=owner_ref,
            payload=payload_obj,
            provenance=provenance,
            external_refs=external_refs,
        )


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, frozenset | set):
        return [_jsonable(item) for item in sorted(value, key=repr)]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _nullable_string(value: object) -> str | None:
    return None if value is None else str(value)
