"""Durable stdlib SQLite baseline for the canonical kernel repository."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    OperationContext,
    OperationControl,
    PlatformEvent,
    RetryMode,
)
from ai_multi_agent_platform.contracts.types import JsonValue

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
                "SELECT event_json FROM kernel_events "
                "WHERE stream_id = ? ORDER BY sequence ASC",
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
                if event.context.correlation_id != stream_id:
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
                        event.event_id,
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
        control = event.context.control
        payload: dict[str, object] = {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "subject_type": event.subject_type,
            "subject_id": event.subject_id,
            "occurred_at": event.occurred_at,
            "schema_version": event.schema_version,
            "context": {
                "correlation_id": event.context.correlation_id,
                "causation_id": event.context.causation_id,
                "owner_type": event.context.owner_type,
                "owner_id": event.context.owner_id,
                "project_id": event.context.project_id,
                "control": {
                    "timeout_seconds": control.timeout_seconds,
                    "idempotency_key": control.idempotency_key,
                    "retry_mode": control.retry_mode.value,
                },
            },
            "payload": event.payload,
            "adapter_metadata": [
                {"namespace": item.namespace, "values": item.values}
                for item in event.adapter_metadata
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _decode_event(raw: str) -> PlatformEvent:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("stored event must be a JSON object")
        context_obj = obj.get("context")
        payload_obj = obj.get("payload")
        metadata_obj = obj.get("adapter_metadata", [])
        if not isinstance(context_obj, dict) or not isinstance(payload_obj, dict):
            raise ValueError("stored event has invalid context or payload")
        control_obj = context_obj.get("control", {})
        if not isinstance(control_obj, dict) or not isinstance(metadata_obj, list):
            raise ValueError("stored event has invalid control or adapter metadata")

        adapter_metadata: list[AdapterMetadata] = []
        for item in metadata_obj:
            if not isinstance(item, dict) or not isinstance(item.get("values"), dict):
                raise ValueError("stored adapter metadata is invalid")
            adapter_metadata.append(
                AdapterMetadata(
                    namespace=str(item["namespace"]),
                    values=cast(dict[str, JsonValue], item["values"]),
                )
            )

        return PlatformEvent(
            event_id=str(obj["event_id"]),
            event_type=str(obj["event_type"]),
            subject_type=str(obj["subject_type"]),
            subject_id=str(obj["subject_id"]),
            occurred_at=str(obj["occurred_at"]),
            schema_version=str(obj["schema_version"]),
            context=OperationContext(
                correlation_id=str(context_obj["correlation_id"]),
                causation_id=_nullable_string(context_obj.get("causation_id")),
                owner_type=_nullable_string(context_obj.get("owner_type")),
                owner_id=_nullable_string(context_obj.get("owner_id")),
                project_id=_nullable_string(context_obj.get("project_id")),
                control=OperationControl(
                    timeout_seconds=_nullable_float(control_obj.get("timeout_seconds")),
                    idempotency_key=_nullable_string(control_obj.get("idempotency_key")),
                    retry_mode=RetryMode(str(control_obj.get("retry_mode", RetryMode.NEVER.value))),
                ),
            ),
            payload=cast(dict[str, JsonValue], payload_obj),
            adapter_metadata=tuple(adapter_metadata),
        )


def _nullable_string(value: object) -> str | None:
    return None if value is None else str(value)


def _nullable_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("stored timeout_seconds must be numeric or null")
    return float(value)
