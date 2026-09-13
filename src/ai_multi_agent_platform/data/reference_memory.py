"""Local MemoryProvider reference implementation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.domain import validate_id

from .contracts import MemoryProvider
from .models import (
    DataAccessContext,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from .reference_support import SqliteReferenceStore as _SqliteMixin
from .reference_support import actor_ref as _actor_ref
from .reference_support import compat_context as _compat_context
from .reference_support import forbidden as _forbidden
from .reference_support import json_dict as _json_dict
from .reference_support import json_dump as _json_dump
from .reference_support import json_value as _json_value
from .reference_support import not_found as _not_found
from .reference_support import optional_time as _optional_time
from .reference_support import parse_time as _parse_time

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class _MemorySqliteOffload:
    """Bound blocking Memory SQLite operations without using the shared executor."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="memory-sqlite",
        )
        self._write_lock = threading.Lock()

    async def run[T](self, operation: Callable[[], T], *, write: bool = False) -> T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(self._executor, self._run_sync, operation, write)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            failure = worker.exception()
            if failure is not None:
                raise failure from None
            raise

    def _run_sync[T](self, operation: Callable[[], T], write: bool) -> T:
        if write:
            with self._write_lock:
                return operation()
        return operation()


class LocalMemoryProvider(_SqliteMixin, MemoryProvider):
    """SQLite scoped-memory provider with non-blocking runtime persistence."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._memory_offload = _MemorySqliteOffload(max_concurrency=max_concurrency)
        capability = Capability(
            name="local-scoped-memory",
            kind=CapabilityKind.MEMORY,
            supported_operations=(
                "write",
                "get",
                "query",
                "search",
                "supersede",
                "delete",
                "expire",
            ),
            features=("six_scopes", "keyword_search", "provenance", "expiry"),
        )
        self._descriptor = ProviderDescriptor(
            provider_id="local-memory-reference",
            provider_type="memory",
            supported_operations=capability.supported_operations,
            capabilities=(capability,),
            health=HealthStatus.HEALTHY,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_memory (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    owner_ref TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    retention TEXT NOT NULL,
                    expires_at TEXT,
                    provenance_json TEXT NOT NULL,
                    supersedes_memory_id TEXT,
                    superseded_by_memory_id TEXT,
                    classification TEXT,
                    metadata_json TEXT NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS data_memory_scope_idx ON data_memory(scope, scope_id)"
            )

    async def _run_memory_sqlite[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._memory_offload.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise _map_memory_sqlite_error(exc, message) from exc

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        self._check_scope(entry.scope, entry.scope_id, context)

        def operation() -> MemoryEntry:
            try:
                with self._connect() as connection:
                    self._insert_entry(connection, entry)
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"memory entry already exists: {entry.memory_id}",
                ) from exc
            return entry

        return await self._run_memory_sqlite(
            operation,
            message="failed to persist memory entry",
            write=True,
        )

    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry:
        validate_id(memory_id, "memory")

        def operation() -> MemoryEntry | None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_memory WHERE memory_id = ? AND deleted = 0",
                    (memory_id,),
                ).fetchone()
            return None if row is None else self._memory_from_row(row)

        entry = await self._run_memory_sqlite(
            operation,
            message="failed to read memory entry",
        )
        if entry is None:
            raise _not_found("memory", memory_id)
        self._check_scope(entry.scope, entry.scope_id, context)
        if entry.expired:
            raise _not_found("memory", memory_id)
        return entry

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        self._check_scope(query.scope, query.scope_id, context)

        def operation() -> tuple[MemoryEntry, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM data_memory
                    WHERE scope = ? AND scope_id = ? AND deleted = 0
                    ORDER BY created_at DESC
                    """,
                    (query.scope.value, query.scope_id),
                ).fetchall()
            return tuple(self._memory_from_row(row) for row in rows)

        stored = await self._run_memory_sqlite(
            operation,
            message="failed to query memory entries",
        )
        now = datetime.now(UTC)
        entries: list[MemoryEntry] = []
        for entry in stored:
            if query.owner_ref is not None and entry.owner_ref != query.owner_ref:
                continue
            if (
                not query.include_expired
                and entry.expires_at is not None
                and entry.expires_at <= now
            ):
                continue
            if not query.include_superseded and entry.superseded_by_memory_id is not None:
                continue
            entries.append(entry)
            if len(entries) >= query.limit:
                break
        return tuple(entries)

    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        needle = text.strip().casefold()
        if not needle:
            raise ContractError(ErrorCode.INVALID_REQUEST, "memory search text must not be blank")
        entries = await self.query_entries(query, context)
        return tuple(
            entry
            for entry in entries
            if needle in _json_dump(entry.value).casefold()
            or needle in _json_dump(entry.metadata).casefold()
        )

    async def supersede_entry(
        self,
        memory_id: str,
        replacement: MemoryEntry,
        context: DataAccessContext,
    ) -> MemoryEntry:
        validate_id(memory_id, "memory")

        def operation() -> MemoryEntry:
            try:
                with self._connect() as connection:
                    row = connection.execute(
                        "SELECT * FROM data_memory WHERE memory_id = ? AND deleted = 0",
                        (memory_id,),
                    ).fetchone()
                    if row is None:
                        raise _not_found("memory", memory_id)
                    current = self._memory_from_row(row)
                    self._check_scope(current.scope, current.scope_id, context)
                    if current.expired:
                        raise _not_found("memory", memory_id)
                    if (
                        replacement.scope is not current.scope
                        or replacement.scope_id != current.scope_id
                    ):
                        raise ContractError(
                            ErrorCode.INVALID_REQUEST,
                            "replacement memory must remain in the same scope",
                        )
                    if replacement.owner_ref != current.owner_ref:
                        raise ContractError(
                            ErrorCode.INVALID_REQUEST,
                            "replacement memory must preserve owner_ref",
                        )
                    if replacement.supersedes_memory_id not in (None, memory_id):
                        raise ContractError(
                            ErrorCode.INVALID_REQUEST,
                            "replacement supersedes a different memory entry",
                        )
                    linked = replace(replacement, supersedes_memory_id=memory_id)
                    self._check_scope(linked.scope, linked.scope_id, context)
                    self._insert_entry(connection, linked)
                    connection.execute(
                        "UPDATE data_memory SET superseded_by_memory_id = ? WHERE memory_id = ?",
                        (linked.memory_id, memory_id),
                    )
            except sqlite3.IntegrityError as exc:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"memory entry already exists: {replacement.memory_id}",
                ) from exc
            return linked

        return await self._run_memory_sqlite(
            operation,
            message="failed to supersede memory entry",
            write=True,
        )

    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None:
        validate_id(memory_id, "memory")

        def operation() -> None:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_memory WHERE memory_id = ? AND deleted = 0",
                    (memory_id,),
                ).fetchone()
                if row is None:
                    raise _not_found("memory", memory_id)
                entry = self._memory_from_row(row)
                self._check_scope(entry.scope, entry.scope_id, context)
                if entry.expired:
                    raise _not_found("memory", memory_id)
                connection.execute(
                    "UPDATE data_memory SET deleted = 1 WHERE memory_id = ?",
                    (memory_id,),
                )

        await self._run_memory_sqlite(
            operation,
            message="failed to delete memory entry",
            write=True,
        )

    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]:
        now = datetime.now(UTC)

        def operation() -> tuple[str, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM data_memory
                    WHERE deleted = 0 AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (now.isoformat(),),
                ).fetchall()
                expired: list[str] = []
                for row in rows:
                    entry = self._memory_from_row(row)
                    try:
                        self._check_scope(entry.scope, entry.scope_id, context)
                    except ContractError as exc:
                        if exc.code is ErrorCode.FORBIDDEN:
                            continue
                        raise
                    expired.append(entry.memory_id)
                if expired:
                    placeholders = ",".join("?" for _ in expired)
                    connection.execute(
                        f"UPDATE data_memory SET deleted = 1 WHERE memory_id IN ({placeholders})",
                        expired,
                    )
            return tuple(expired)

        return await self._run_memory_sqlite(
            operation,
            message="failed to expire memory entries",
            write=True,
        )

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        try:
            scope = MemoryScope(namespace)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, f"unknown memory scope: {namespace}"
            ) from exc
        now = datetime.now(UTC)
        retention = RetentionPolicy.DURABLE
        expires_at: datetime | None = None
        provenance: tuple[SourceRef, ...] = ()
        if scope is MemoryScope.SHORT_TERM:
            retention = RetentionPolicy.EPHEMERAL
            expires_at = now + timedelta(hours=1)
        elif scope is MemoryScope.TASK:
            retention = RetentionPolicy.TASK_LIFETIME
        elif scope is MemoryScope.WORKSPACE:
            retention = RetentionPolicy.PROJECT_LIFETIME
        elif scope is MemoryScope.USER:
            retention = RetentionPolicy.USER_LIFETIME
        elif scope is MemoryScope.HISTORICAL:
            provenance = (SourceRef(kind="correlation", ref=context.correlation_id),)
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=scope,
            scope_id=key,
            owner_ref=_actor_ref(context),
            created_by=_actor_ref(context),
            value=value,
            created_at=now,
            retention=retention,
            expires_at=expires_at,
            provenance=provenance,
            metadata=metadata or {},
        )
        await self.write_entry(entry, _compat_context(context))
        return StoredObject(object_ref=entry.memory_id, metadata={"scope": scope.value})

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        try:
            scope = MemoryScope(namespace)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST, f"unknown memory scope: {namespace}"
            ) from exc
        entries = await self.query_entries(
            MemoryQuery(scope=scope, scope_id=key, limit=1), _compat_context(context)
        )
        if not entries:
            raise _not_found("memory scope/key", f"{namespace}/{key}")
        return entries[0].value

    @staticmethod
    def _check_scope(scope: MemoryScope, scope_id: str, context: DataAccessContext) -> None:
        if scope is MemoryScope.WORKSPACE and context.project_id != scope_id:
            raise _forbidden("workspace memory belongs to a different project/workspace")
        if scope is MemoryScope.USER:
            operation = context.operation
            if operation.owner_type == "user" and operation.owner_id != scope_id:
                raise _forbidden("user memory belongs to a different user scope")

    @staticmethod
    def _insert_entry(connection: sqlite3.Connection, entry: MemoryEntry) -> None:
        provenance = [
            {
                "kind": item.kind,
                "ref": item.ref,
                "location": item.location,
                "revision": item.revision,
                "checksum": item.checksum,
            }
            for item in entry.provenance
        ]
        connection.execute(
            """
            INSERT INTO data_memory (
                memory_id, scope, scope_id, owner_ref, created_by, value_json, created_at,
                retention, expires_at, provenance_json, supersedes_memory_id,
                superseded_by_memory_id, classification, metadata_json, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                entry.memory_id,
                entry.scope.value,
                entry.scope_id,
                entry.owner_ref,
                entry.created_by,
                _json_dump(entry.value),
                entry.created_at.isoformat(),
                entry.retention.value,
                entry.expires_at.isoformat() if entry.expires_at is not None else None,
                _json_dump(provenance),
                entry.supersedes_memory_id,
                entry.superseded_by_memory_id,
                entry.classification,
                _json_dump(entry.metadata),
            ),
        )

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryEntry:
        raw_provenance = json.loads(cast(str, row["provenance_json"]))
        if not isinstance(raw_provenance, list):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored provenance is invalid")
        provenance: list[SourceRef] = []
        for raw_item in raw_provenance:
            if not isinstance(raw_item, dict):
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored provenance is invalid")
            item = cast(dict[str, Any], raw_item)
            provenance.append(
                SourceRef(
                    kind=cast(str, item["kind"]),
                    ref=cast(str, item["ref"]),
                    location=cast(str | None, item.get("location")),
                    revision=cast(str | None, item.get("revision")),
                    checksum=cast(str | None, item.get("checksum")),
                )
            )
        return MemoryEntry(
            memory_id=cast(str, row["memory_id"]),
            scope=MemoryScope(cast(str, row["scope"])),
            scope_id=cast(str, row["scope_id"]),
            owner_ref=cast(str, row["owner_ref"]),
            created_by=cast(str, row["created_by"]),
            value=_json_value(cast(str, row["value_json"])),
            created_at=_parse_time(cast(str, row["created_at"])),
            retention=RetentionPolicy(cast(str, row["retention"])),
            expires_at=_optional_time(cast(str | None, row["expires_at"])),
            provenance=tuple(provenance),
            supersedes_memory_id=cast(str | None, row["supersedes_memory_id"]),
            superseded_by_memory_id=cast(str | None, row["superseded_by_memory_id"]),
            classification=cast(str | None, row["classification"]),
            metadata=_json_dict(cast(str, row["metadata_json"])),
        )


def _map_memory_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)
