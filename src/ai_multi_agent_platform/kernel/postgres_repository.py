"""Shared PostgreSQL persistence adapter for the canonical task/run/event kernel."""

from __future__ import annotations

import asyncio
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, Self, TypeVar, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import Event, ExternalRef, OwnerRef, Provenance

from .repository import CommandRecord, CommitResult, EventRepository

_T = TypeVar("_T")
_SCHEMA_COMPONENT = "kernel"
_SCHEMA_VERSION = 1
_SCHEMA_REVISION = "kernel-v1"
_KERNEL_TABLES = (
    "ai_map_kernel_streams",
    "ai_map_kernel_events",
    "ai_map_kernel_commands",
)


class _Cursor(Protocol):
    def execute(self, query: str, params: Sequence[object] | None = None) -> Self: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def fetchall(self) -> Sequence[Sequence[object]]: ...

    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


ConnectionFactory = Callable[[str], _Connection]


class PostgresKernelRepository(EventRepository):
    """Transactional shared kernel repository for the optional HA persistence profile.

    The adapter preserves the platform-owned ``EventRepository`` contract. PostgreSQL-specific
    identifiers, connections and SQL remain private implementation details. Schema bootstrap is
    explicit so deployment can use a privileged bootstrap identity and a narrower runtime identity.
    """

    def __init__(
        self,
        dsn: str,
        *,
        connect: ConnectionFactory | None = None,
        max_concurrency: int = 8,
    ) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL kernel DSN must not be blank")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._dsn = dsn
        self._connect = connect or _load_psycopg_connect()
        self._slots = asyncio.Semaphore(max_concurrency)

    async def initialize(self) -> None:
        """Create a fresh schema or validate an already-versioned compatible schema."""

        await self._run(self._initialize_schema, message="PostgreSQL kernel schema setup failed")

    async def read_events(self, stream_id: str) -> tuple[PlatformEvent, ...]:
        return await self._run(
            lambda connection: self._read_events(connection, stream_id),
            message="failed to read kernel events",
        )

    async def revision(self, stream_id: str) -> int:
        return await self._run(
            lambda connection: self._revision(connection, stream_id),
            message="failed to read kernel revision",
        )

    async def find_command(self, scope: str, idempotency_key: str) -> CommandRecord | None:
        return await self._run(
            lambda connection: self._find_command(connection, scope, idempotency_key),
            message="failed to read kernel command",
        )

    async def list_stream_ids(self) -> tuple[str, ...]:
        return await self._run(
            self._list_stream_ids,
            message="failed to list kernel streams",
        )

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

        return await self._run(
            lambda connection: self._commit(
                connection,
                stream_id=stream_id,
                expected_revision=expected_revision,
                events=events,
                command=command,
            ),
            message="kernel persistence operation failed",
        )

    async def _run(self, operation: Callable[[_Connection], _T], *, message: str) -> _T:
        async with self._slots:
            worker = asyncio.create_task(asyncio.to_thread(self._run_sync, operation))
            try:
                return await asyncio.shield(worker)
            except asyncio.CancelledError:
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        continue
                try:
                    worker.result()
                except Exception:
                    pass
                raise
            except ContractError:
                raise
            # error-boundary: allow-broad-catch=translation reviewed error translation
            except Exception as exc:
                raise _map_postgres_error(exc, message) from None

    def _run_sync(self, operation: Callable[[_Connection], _T]) -> _T:
        connection = self._connect(self._dsn)
        try:
            result = operation(connection)
            connection.commit()
            return result
        # error-boundary: allow-broad-catch=cleanup reviewed cleanup boundary
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize_schema(self, connection: _Connection) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_map_ha_schema_versions (
                    component TEXT PRIMARY KEY,
                    schema_version INTEGER NOT NULL CHECK (schema_version > 0),
                    migration_revision TEXT NOT NULL
                )
                """
            )
            cursor.execute(
                """
                SELECT schema_version, migration_revision
                FROM ai_map_ha_schema_versions
                WHERE component = %s
                FOR UPDATE
                """,
                (_SCHEMA_COMPONENT,),
            )
            metadata = cursor.fetchone()

            if metadata is None:
                existing_tables = tuple(
                    table for table in _KERNEL_TABLES if self._table_exists(cursor, table)
                )
                if existing_tables:
                    raise ContractError(
                        ErrorCode.INVALID_CONFIGURATION,
                        "partial PostgreSQL kernel schema exists without version metadata",
                        details={"component": _SCHEMA_COMPONENT},
                    )
                self._create_kernel_tables(cursor)
                cursor.execute(
                    """
                    INSERT INTO ai_map_ha_schema_versions(
                        component, schema_version, migration_revision
                    ) VALUES (%s, %s, %s)
                    """,
                    (_SCHEMA_COMPONENT, _SCHEMA_VERSION, _SCHEMA_REVISION),
                )
                return

            if len(metadata) != 2:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "PostgreSQL kernel schema metadata is malformed",
                )
            actual_version = metadata[0]
            actual_revision = metadata[1]
            if actual_version != _SCHEMA_VERSION or actual_revision != _SCHEMA_REVISION:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "PostgreSQL kernel schema is incompatible with this platform release",
                    details={
                        "component": _SCHEMA_COMPONENT,
                        "expected_schema_version": _SCHEMA_VERSION,
                        "actual_schema_version": (
                            actual_version if isinstance(actual_version, int) else -1
                        ),
                    },
                )
            self._validate_kernel_tables(cursor)

    @staticmethod
    def _table_exists(cursor: _Cursor, table: str) -> bool:
        cursor.execute("SELECT to_regclass(%s)", (table,))
        row = cursor.fetchone()
        return row is not None and len(row) == 1 and row[0] is not None

    @staticmethod
    def _create_kernel_tables(cursor: _Cursor) -> None:
        cursor.execute(
            """
            CREATE TABLE ai_map_kernel_streams (
                stream_id TEXT PRIMARY KEY,
                revision BIGINT NOT NULL CHECK (revision >= 0)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE ai_map_kernel_events (
                stream_id TEXT NOT NULL,
                sequence BIGINT NOT NULL CHECK (sequence > 0),
                event_id TEXT NOT NULL UNIQUE,
                event_json TEXT NOT NULL,
                PRIMARY KEY (stream_id, sequence),
                FOREIGN KEY (stream_id) REFERENCES ai_map_kernel_streams(stream_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX ai_map_kernel_events_stream_idx
            ON ai_map_kernel_events(stream_id, sequence)
            """
        )
        cursor.execute(
            """
            CREATE TABLE ai_map_kernel_commands (
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

    @staticmethod
    def _validate_kernel_tables(cursor: _Cursor) -> None:
        try:
            cursor.execute("SELECT stream_id, revision FROM ai_map_kernel_streams LIMIT 0")
            cursor.execute(
                """
                SELECT stream_id, sequence, event_id, event_json
                FROM ai_map_kernel_events
                LIMIT 0
                """
            )
            cursor.execute(
                """
                SELECT scope, idempotency_key, operation, stream_id, result_id, event_id
                FROM ai_map_kernel_commands
                LIMIT 0
                """
            )
        # error-boundary: allow-broad-catch=translation reviewed canonical/domain error translation
        except Exception:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "PostgreSQL kernel schema is incomplete or incompatible",
            ) from None

    def _read_events(self, connection: _Connection, stream_id: str) -> tuple[PlatformEvent, ...]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT event_json
                FROM ai_map_kernel_events
                WHERE stream_id = %s
                ORDER BY sequence ASC
                """,
                (stream_id,),
            )
            rows = cursor.fetchall()
        return tuple(_decode_event(_as_str(row[0], "kernel event JSON")) for row in rows)

    def _revision(self, connection: _Connection, stream_id: str) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision FROM ai_map_kernel_streams WHERE stream_id = %s",
                (stream_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return 0
        if len(row) != 1:
            raise ContractError(ErrorCode.BACKEND_ERROR, "kernel revision row is malformed")
        return _as_int(row[0], "kernel revision")

    def _find_command(
        self,
        connection: _Connection,
        scope: str,
        idempotency_key: str,
    ) -> CommandRecord | None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT scope, idempotency_key, operation, stream_id, result_id, event_id
                FROM ai_map_kernel_commands
                WHERE scope = %s AND idempotency_key = %s
                """,
                (scope, idempotency_key),
            )
            row = cursor.fetchone()
        return None if row is None else _command_from_row(row)

    def _list_stream_ids(self, connection: _Connection) -> tuple[str, ...]:
        with connection.cursor() as cursor:
            cursor.execute("SELECT stream_id FROM ai_map_kernel_streams ORDER BY stream_id")
            rows = cursor.fetchall()
        return tuple(_as_str(row[0], "kernel stream id") for row in rows)

    def _commit(
        self,
        connection: _Connection,
        *,
        stream_id: str,
        expected_revision: int,
        events: tuple[PlatformEvent, ...],
        command: CommandRecord | None,
    ) -> CommitResult:
        with connection.cursor() as cursor:
            if command is not None:
                cursor.execute(
                    """
                    INSERT INTO ai_map_kernel_commands(
                        scope, idempotency_key, operation, stream_id, result_id, event_id
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (scope, idempotency_key) DO NOTHING
                    RETURNING scope, idempotency_key, operation, stream_id, result_id, event_id
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
                reserved = cursor.fetchone()
                if reserved is None:
                    cursor.execute(
                        """
                        SELECT scope, idempotency_key, operation, stream_id, result_id, event_id
                        FROM ai_map_kernel_commands
                        WHERE scope = %s AND idempotency_key = %s
                        """,
                        (command.scope, command.idempotency_key),
                    )
                    existing_row = cursor.fetchone()
                    if existing_row is None:
                        raise ContractError(
                            ErrorCode.BACKEND_ERROR,
                            "kernel command reservation disappeared during transaction",
                        )
                    existing = _command_from_row(existing_row)
                    cursor.execute(
                        "SELECT revision FROM ai_map_kernel_streams WHERE stream_id = %s",
                        (existing.stream_id,),
                    )
                    revision_row = cursor.fetchone()
                    revision = (
                        0 if revision_row is None else _as_int(revision_row[0], "kernel revision")
                    )
                    return CommitResult(
                        applied=False,
                        revision=revision,
                        command=existing,
                    )

            cursor.execute(
                """
                INSERT INTO ai_map_kernel_streams(stream_id, revision)
                VALUES (%s, 0)
                ON CONFLICT (stream_id) DO NOTHING
                """,
                (stream_id,),
            )
            cursor.execute(
                "SELECT revision FROM ai_map_kernel_streams WHERE stream_id = %s FOR UPDATE",
                (stream_id,),
            )
            revision_row = cursor.fetchone()
            if revision_row is None:
                raise ContractError(ErrorCode.BACKEND_ERROR, "kernel stream reservation is missing")
            actual_revision = _as_int(revision_row[0], "kernel revision")
            if actual_revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"stale stream revision for {stream_id}: "
                    f"expected {expected_revision}, actual {actual_revision}",
                    retryable=True,
                    details={
                        "reason": "stale_stream_revision",
                        "expected_revision": expected_revision,
                        "actual_revision": actual_revision,
                    },
                )

            for offset, event in enumerate(events, start=1):
                if event.correlation_id != stream_id:
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        "event correlation_id must equal canonical stream id",
                    )
                cursor.execute(
                    """
                    INSERT INTO ai_map_kernel_events(stream_id, sequence, event_id, event_json)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        stream_id,
                        expected_revision + offset,
                        event.id,
                        _encode_event(event),
                    ),
                )

            next_revision = expected_revision + len(events)
            cursor.execute(
                "UPDATE ai_map_kernel_streams SET revision = %s WHERE stream_id = %s",
                (next_revision, stream_id),
            )
            return CommitResult(
                applied=True,
                revision=next_revision,
                command=command,
            )


def _load_psycopg_connect() -> ConnectionFactory:
    try:
        module = importlib.import_module("psycopg")
    except ImportError:
        raise RuntimeError(
            "PostgreSQL HA persistence requires the optional 'ha-postgres' dependency"
        ) from None
    connection_factory = getattr(module, "connect", None)
    if not callable(connection_factory):
        raise RuntimeError(
            "PostgreSQL HA persistence requires a compatible Psycopg installation"
        ) from None
    return cast(ConnectionFactory, connection_factory)


def _map_postgres_error(exc: Exception, message: str) -> ContractError:
    raw_state = getattr(exc, "sqlstate", None)
    sqlstate = raw_state if isinstance(raw_state, str) else None
    if sqlstate == "23505":
        return ContractError(ErrorCode.CONFLICT, message)
    if sqlstate in {"40001", "40P01", "55P03"}:
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    if sqlstate is not None and (sqlstate.startswith("08") or sqlstate in {"57P01", "57P03"}):
        return ContractError(ErrorCode.UNAVAILABLE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def _command_from_row(row: Sequence[object]) -> CommandRecord:
    if len(row) != 6:
        raise ContractError(ErrorCode.BACKEND_ERROR, "kernel command row is malformed")
    return CommandRecord(
        scope=_as_str(row[0], "kernel command scope"),
        idempotency_key=_as_str(row[1], "kernel idempotency key"),
        operation=_as_str(row[2], "kernel command operation"),
        stream_id=_as_str(row[3], "kernel command stream id"),
        result_id=_as_str(row[4], "kernel command result id"),
        event_id=_as_str(row[5], "kernel command event id"),
    )


def _as_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.BACKEND_ERROR, f"{field} has invalid type")
    return value


def _as_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ContractError(ErrorCode.BACKEND_ERROR, f"{field} has invalid type")
    return value


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
