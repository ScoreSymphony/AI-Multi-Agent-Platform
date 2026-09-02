"""Small durable EventProvider used for kernel recovery and integration tests."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    EventProvider,
)
from ai_multi_agent_platform.contracts.types import (
    JsonValue,
    OperationContext,
    OperationControl,
    PlatformEvent,
    ProviderDescriptor,
)


class SqliteEventProvider(EventProvider):
    """Durable stdlib-only event provider for tests and local reference flows."""

    descriptor = ProviderDescriptor(
        provider_id="sqlite-event-reference",
        provider_type="event",
        supported_operations=("publish", "read", "subscribe"),
        capabilities=(Capability(name="event.durable", kind=CapabilityKind.EVENT),),
    )

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS platform_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    owner_type TEXT,
                    owner_id TEXT,
                    project_id TEXT,
                    schema_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_events_correlation_sequence
                ON platform_events(correlation_id, sequence)
                """
            )

    async def publish(self, event: PlatformEvent) -> None:
        """Append once by event ID so deterministic command reservations are atomic."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO platform_events (
                    event_id, event_type, subject_type, subject_id, occurred_at,
                    correlation_id, causation_id, owner_type, owner_id, project_id,
                    schema_version, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.subject_type,
                    event.subject_id,
                    event.occurred_at,
                    event.context.correlation_id,
                    event.context.causation_id,
                    event.context.owner_type,
                    event.context.owner_id,
                    event.context.project_id,
                    event.schema_version,
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                ),
            )

    async def read(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> tuple[PlatformEvent, ...]:
        del control
        query = """
            SELECT sequence, event_id, event_type, subject_type, subject_id, occurred_at,
                   correlation_id, causation_id, owner_type, owner_id, project_id,
                   schema_version, payload_json
            FROM platform_events
            WHERE correlation_id = ?
        """
        parameters: list[str | int] = [correlation_id]

        if after_event_id is not None:
            cursor_query = (
                "SELECT sequence FROM platform_events "
                "WHERE event_id = ? AND correlation_id = ?"
            )
            with self._connect() as connection:
                row = connection.execute(
                    cursor_query,
                    (after_event_id, correlation_id),
                ).fetchone()
            if row is None:
                message = f"Event cursor not found: {after_event_id}"
                raise ContractError(ErrorCode.NOT_FOUND, message)
            query += " AND sequence > ?"
            parameters.append(int(row["sequence"]))

        query += " ORDER BY sequence ASC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return tuple(self._to_event(row) for row in rows)

    def subscribe(
        self,
        correlation_id: str,
        *,
        after_event_id: str | None = None,
        control: OperationControl | None = None,
    ) -> AsyncIterator[PlatformEvent]:
        async def iterator() -> AsyncIterator[PlatformEvent]:
            for event in await self.read(
                correlation_id,
                after_event_id=after_event_id,
                control=control,
            ):
                yield event

        return iterator()

    @staticmethod
    def _nullable_text(value: object) -> str | None:
        return None if value is None else str(value)

    @classmethod
    def _to_event(cls, row: sqlite3.Row) -> PlatformEvent:
        payload_object = json.loads(str(row["payload_json"]))
        if not isinstance(payload_object, dict):
            raise ValueError("Stored event payload must be a JSON object")
        payload = cast(dict[str, JsonValue], payload_object)
        return PlatformEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            subject_type=str(row["subject_type"]),
            subject_id=str(row["subject_id"]),
            occurred_at=str(row["occurred_at"]),
            context=OperationContext(
                correlation_id=str(row["correlation_id"]),
                causation_id=cls._nullable_text(row["causation_id"]),
                owner_type=cls._nullable_text(row["owner_type"]),
                owner_id=cls._nullable_text(row["owner_id"]),
                project_id=cls._nullable_text(row["project_id"]),
            ),
            payload=payload,
            schema_version=str(row["schema_version"]),
        )
