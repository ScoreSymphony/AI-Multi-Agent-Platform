"""Simple self-hosted reference implementations for issue #13 data contracts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import AsyncIterator
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
    KnowledgeHit,
    KnowledgeQuery,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.domain import validate_id

from .contracts import FileProvider, KnowledgeProvider, MemoryProvider
from .models import (
    DataAccessContext,
    FileRecord,
    FileState,
    IndexReference,
    KnowledgeDocument,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    KnowledgeStatus,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    OrphanReport,
    RetentionPolicy,
    SourceRef,
    new_file_id,
    new_knowledge_document_id,
    new_knowledge_index_id,
    new_memory_id,
)


def _json_dump(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_value(value: str) -> JsonValue:
    return cast(JsonValue, json.loads(value))


def _json_dict(value: str) -> dict[str, JsonValue]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored metadata is not an object")
    return cast(dict[str, JsonValue], loaded)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored timestamp is not timezone-aware")
    return parsed


def _optional_time(value: str | None) -> datetime | None:
    return None if value is None else _parse_time(value)


def _actor_ref(operation: OperationContext) -> str:
    if operation.owner_type is not None and operation.owner_id is not None:
        return f"{operation.owner_type}:{operation.owner_id}"
    return "service:unspecified"


def _compat_context(operation: OperationContext) -> DataAccessContext:
    return DataAccessContext(operation=operation, actor_ref=_actor_ref(operation))


def _forbidden(message: str) -> ContractError:
    return ContractError(ErrorCode.FORBIDDEN, message)


def _not_found(kind: str, ref: str) -> ContractError:
    return ContractError(ErrorCode.NOT_FOUND, f"{kind} not found: {ref}")


class _SqliteMixin:
    _db_path: Path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


class LocalFileProvider(_SqliteMixin, FileProvider):
    """Filesystem bytes with SQLite metadata and canonical file IDs."""

    def __init__(self, root: str | Path, db_path: str | Path) -> None:
        self._root = Path(root)
        self._db_path = Path(db_path)
        self._root.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        capability = Capability(
            name="local-files",
            kind=CapabilityKind.FILE,
            supported_operations=(
                "create",
                "read",
                "stream",
                "metadata",
                "list",
                "delete",
                "checksum",
                "artifact_link",
                "orphan_detection",
            ),
            features=("sha256", "tombstones", "project_scope"),
        )
        self._descriptor = ProviderDescriptor(
            provider_id="local-file-reference",
            provider_type="file",
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
                CREATE TABLE IF NOT EXISTS data_files (
                    file_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    owner_ref TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    content_type TEXT,
                    artifact_ids_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )

    async def create_file(
        self,
        data: bytes,
        context: DataAccessContext,
        *,
        file_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> FileRecord:
        canonical_id = file_id or new_file_id()
        validate_id(canonical_id, "file")
        digest = hashlib.sha256(data).hexdigest()
        now = datetime.now(UTC)
        pending = FileRecord(
            file_id=canonical_id,
            project_id=context.project_id,
            owner_ref=context.actor_ref,
            created_by=context.actor_ref,
            created_at=now,
            size_bytes=len(data),
            sha256=digest,
            state=FileState.PENDING,
            content_type=content_type,
            metadata=metadata or {},
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_files (
                        file_id, project_id, owner_ref, created_by, created_at, size_bytes,
                        sha256, state, content_type, artifact_ids_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pending.file_id,
                        pending.project_id,
                        pending.owner_ref,
                        pending.created_by,
                        pending.created_at.isoformat(),
                        pending.size_bytes,
                        pending.sha256,
                        pending.state.value,
                        pending.content_type,
                        "[]",
                        _json_dump(pending.metadata),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(ErrorCode.CONFLICT, f"file already exists: {canonical_id}") from exc
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist file metadata") from exc

        final_path = self._root / canonical_id
        temp_path = self._root / f".{canonical_id}.pending"
        try:
            temp_path.write_bytes(data)
            os.replace(temp_path, final_path)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.READY.value, canonical_id),
                )
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, canonical_id),
                )
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist file bytes") from exc
        except sqlite3.Error as exc:
            final_path.unlink(missing_ok=True)
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, canonical_id),
                )
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to finalize file metadata"
            ) from exc
        return replace(pending, state=FileState.READY)

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        record = await self.create_file(
            data,
            _compat_context(context),
            file_id=object_ref,
            metadata=metadata,
        )
        return StoredObject(
            object_ref=record.file_id,
            metadata={"sha256": record.sha256, "size_bytes": record.size_bytes},
        )

    async def get_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        validate_id(file_id, "file")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_files WHERE file_id = ?",
                    (file_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read file metadata") from exc
        if row is None:
            raise _not_found("file", file_id)
        record = self._file_from_row(row)
        if record.state is FileState.TOMBSTONED:
            raise _not_found("file", file_id)
        self._check_project(record.project_id, context)
        return record

    async def list_files(self, context: DataAccessContext) -> tuple[FileRecord, ...]:
        try:
            with self._connect() as connection:
                if context.project_id is None:
                    rows = connection.execute(
                        "SELECT * FROM data_files WHERE project_id IS NULL "
                        "AND state != ? ORDER BY created_at",
                        (FileState.TOMBSTONED.value,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM data_files WHERE project_id = ? "
                        "AND state != ? ORDER BY created_at",
                        (context.project_id, FileState.TOMBSTONED.value),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to list files") from exc
        return tuple(self._file_from_row(row) for row in rows)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        access = _compat_context(context)
        record = await self.get_file(object_ref, access)
        path = self._root / record.file_id
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise _not_found("file object", record.file_id) from exc
        except OSError as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read file bytes") from exc
        if hashlib.sha256(data).hexdigest() != record.sha256:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"checksum mismatch for file: {record.file_id}",
            )
        return data

    async def stream_file(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, "chunk_size must be greater than zero")
        data = await self.read(file_id, context.operation)
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    async def delete_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        record = await self.get_file(file_id, context)
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_files SET state = ? WHERE file_id = ?",
                    (FileState.TOMBSTONED.value, file_id),
                )
            (self._root / file_id).unlink(missing_ok=True)
        except (sqlite3.Error, OSError) as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to tombstone file") from exc
        return replace(record, state=FileState.TOMBSTONED)

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        record = await self.get_file(file_id, context)
        path = self._root / record.file_id
        try:
            if not path.exists():
                return False
            return hashlib.sha256(path.read_bytes()).hexdigest() == record.sha256
        except OSError as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to verify file checksum") from exc

    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord:
        validate_id(artifact_id, "artifact")
        record = await self.get_file(file_id, context)
        artifact_ids = record.artifact_ids
        if artifact_id not in artifact_ids:
            artifact_ids = (*artifact_ids, artifact_id)
            try:
                with self._connect() as connection:
                    connection.execute(
                        "UPDATE data_files SET artifact_ids_json = ? WHERE file_id = ?",
                        (_json_dump(list(artifact_ids)), file_id),
                    )
            except sqlite3.Error as exc:
                raise ContractError(ErrorCode.BACKEND_ERROR, "failed to link artifact") from exc
        return replace(record, artifact_ids=artifact_ids)

    async def detect_orphans(self, context: DataAccessContext) -> OrphanReport:
        records = await self.list_files(context)
        known = {record.file_id for record in records}
        missing = tuple(sorted(file_id for file_id in known if not (self._root / file_id).exists()))
        unreferenced = tuple(
            sorted(
                path.name
                for path in self._root.iterdir()
                if path.is_file() and path.name.startswith("file_") and path.name not in known
            )
        )
        return OrphanReport(missing_objects=missing, unreferenced_objects=unreferenced)

    @staticmethod
    def _check_project(project_id: str | None, context: DataAccessContext) -> None:
        if project_id != context.project_id:
            raise _forbidden("file belongs to a different project/workspace scope")

    @staticmethod
    def _file_from_row(row: sqlite3.Row) -> FileRecord:
        raw_artifacts = json.loads(cast(str, row["artifact_ids_json"]))
        if not isinstance(raw_artifacts, list) or not all(
            isinstance(item, str) for item in raw_artifacts
        ):
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored artifact IDs are invalid")
        return FileRecord(
            file_id=cast(str, row["file_id"]),
            project_id=cast(str | None, row["project_id"]),
            owner_ref=cast(str, row["owner_ref"]),
            created_by=cast(str, row["created_by"]),
            created_at=_parse_time(cast(str, row["created_at"])),
            size_bytes=cast(int, row["size_bytes"]),
            sha256=cast(str, row["sha256"]),
            state=FileState(cast(str, row["state"])),
            content_type=cast(str | None, row["content_type"]),
            artifact_ids=tuple(cast(list[str], raw_artifacts)),
            metadata=_json_dict(cast(str, row["metadata_json"])),
        )


class LocalMemoryProvider(_SqliteMixin, MemoryProvider):
    """SQLite scoped-memory provider with no vector/embedding requirement."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
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

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        self._check_scope(entry.scope, entry.scope_id, context)
        try:
            with self._connect() as connection:
                self._insert_entry(connection, entry)
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"memory entry already exists: {entry.memory_id}",
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist memory entry") from exc
        return entry

    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry:
        validate_id(memory_id, "memory")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_memory WHERE memory_id = ? AND deleted = 0",
                    (memory_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read memory entry") from exc
        if row is None:
            raise _not_found("memory", memory_id)
        entry = self._memory_from_row(row)
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
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM data_memory
                    WHERE scope = ? AND scope_id = ? AND deleted = 0
                    ORDER BY created_at DESC
                    """,
                    (query.scope.value, query.scope_id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to query memory entries") from exc
        now = datetime.now(UTC)
        entries: list[MemoryEntry] = []
        for row in rows:
            entry = self._memory_from_row(row)
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
        current = await self.get_entry(memory_id, context)
        if replacement.scope is not current.scope or replacement.scope_id != current.scope_id:
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
        try:
            with self._connect() as connection:
                self._insert_entry(connection, linked)
                connection.execute(
                    "UPDATE data_memory SET superseded_by_memory_id = ? WHERE memory_id = ?",
                    (linked.memory_id, memory_id),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"memory entry already exists: {linked.memory_id}",
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to supersede memory entry"
            ) from exc
        return linked

    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None:
        await self.get_entry(memory_id, context)
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_memory SET deleted = 1 WHERE memory_id = ?",
                    (memory_id,),
                )
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to delete memory entry") from exc

    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]:
        now = datetime.now(UTC)
        try:
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
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to expire memory entries") from exc
        return tuple(expired)

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


class LocalKnowledgeProvider(_SqliteMixin, KnowledgeProvider):
    """SQLite source registry with deterministic keyword retrieval."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        capability = Capability(
            name="local-keyword-knowledge",
            kind=CapabilityKind.KNOWLEDGE,
            supported_operations=(
                "register_source",
                "ingest",
                "index_status",
                "keyword_search",
                "reindex",
                "remove",
            ),
            features=("keyword_search", "citations", "source_revisions"),
        )
        self._descriptor = ProviderDescriptor(
            provider_id="local-knowledge-reference",
            provider_type="knowledge",
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
                CREATE TABLE IF NOT EXISTS data_knowledge_sources (
                    source_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    owner_ref TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    title TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    content_checksum TEXT,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_knowledge_documents (
                    document_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    content TEXT NOT NULL,
                    location TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES data_knowledge_sources(source_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS data_knowledge_indexes (
                    index_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL UNIQUE,
                    revision TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES data_knowledge_sources(source_id)
                )
                """
            )

    async def register_source(
        self,
        source: KnowledgeSource,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        self._check_project(source.project_id, context)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_knowledge_sources (
                        source_id, project_id, owner_ref, created_by, title, revision,
                        status, created_at, updated_at, content_checksum, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.source_id,
                        source.project_id,
                        source.owner_ref,
                        source.created_by,
                        source.title,
                        source.revision,
                        source.status.value,
                        source.created_at.isoformat(),
                        source.updated_at.isoformat(),
                        source.content_checksum,
                        _json_dump(source.metadata),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO data_knowledge_indexes (
                        index_id, source_id, revision, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        new_knowledge_index_id(),
                        source.source_id,
                        source.revision,
                        KnowledgeStatus.REGISTERED.value,
                        source.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"knowledge source already exists: {source.source_id}",
            ) from exc
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to register knowledge source"
            ) from exc
        return source

    async def ingest_source(
        self,
        source_id: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        source = await self._get_source(source_id, context)
        if source.status is KnowledgeStatus.REMOVED:
            raise _not_found("knowledge source", source_id)
        document = KnowledgeDocument(
            document_id=new_knowledge_document_id(),
            source_id=source_id,
            revision=source.revision,
            content=content,
            location=location,
            checksum=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            created_at=datetime.now(UTC),
        )
        now = datetime.now(UTC)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO data_knowledge_documents (
                        document_id, source_id, revision, content, location, checksum, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        document.source_id,
                        document.revision,
                        document.content,
                        document.location,
                        document.checksum,
                        document.created_at.isoformat(),
                    ),
                )
                connection.execute(
                    """
                    UPDATE data_knowledge_sources
                    SET status = ?, content_checksum = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (KnowledgeStatus.READY.value, document.checksum, now.isoformat(), source_id),
                )
                connection.execute(
                    """
                    UPDATE data_knowledge_indexes
                    SET revision = ?, status = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        source.revision,
                        KnowledgeStatus.READY.value,
                        now.isoformat(),
                        source_id,
                    ),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to ingest knowledge source"
            ) from exc
        return document

    async def get_index_status(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> IndexReference:
        source = await self._get_source(source_id, context)
        if source.status is KnowledgeStatus.REMOVED:
            raise _not_found("knowledge source", source_id)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_knowledge_indexes WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read knowledge index") from exc
        if row is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"missing canonical knowledge index: {source_id}",
            )
        return IndexReference(
            index_id=cast(str, row["index_id"]),
            source_id=cast(str, row["source_id"]),
            revision=cast(str, row["revision"]),
            status=KnowledgeStatus(cast(str, row["status"])),
            updated_at=_parse_time(cast(str, row["updated_at"])),
        )

    async def search(
        self,
        request: KnowledgeSearchRequest,
    ) -> tuple[KnowledgeSearchResult, ...]:
        if request.mode is not KnowledgeSearchMode.KEYWORD:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"local knowledge provider does not support {request.mode.value} search",
            )
        source_ids = request.source_ids
        sources: tuple[KnowledgeSource, ...]
        if source_ids:
            resolved_sources: list[KnowledgeSource] = []
            for source_id in source_ids:
                resolved_sources.append(await self._get_source(source_id, request.context))
            sources = tuple(resolved_sources)
        else:
            sources = await self._list_sources(request.context)
        active = tuple(source for source in sources if source.status is not KnowledgeStatus.REMOVED)
        if not active:
            return ()
        terms = tuple(term.casefold() for term in request.query.split() if term.strip())
        if not terms:
            return ()
        source_by_id = {source.source_id: source for source in active}
        placeholders = ",".join("?" for _ in source_by_id)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""
                    SELECT * FROM data_knowledge_documents
                    WHERE source_id IN ({placeholders})
                    ORDER BY created_at DESC
                    """,
                    tuple(source_by_id),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to search knowledge") from exc
        results: list[KnowledgeSearchResult] = []
        for row in rows:
            source_id = cast(str, row["source_id"])
            source = source_by_id[source_id]
            revision = cast(str, row["revision"])
            if revision != source.revision:
                continue
            content = cast(str, row["content"])
            haystack = content.casefold()
            matched = sum(1 for term in terms if term in haystack)
            if matched == 0:
                continue
            score = matched / len(terms)
            document_id = cast(str, row["document_id"])
            checksum = cast(str, row["checksum"])
            location = cast(str, row["location"])
            results.append(
                KnowledgeSearchResult(
                    source_id=source_id,
                    document_id=document_id,
                    revision=revision,
                    content=content,
                    location=location,
                    score=score,
                    citation=SourceRef(
                        kind="knowledge_document",
                        ref=document_id,
                        location=location,
                        revision=revision,
                        checksum=checksum,
                    ),
                )
            )
        results.sort(key=lambda item: (item.score or 0.0, item.document_id), reverse=True)
        return tuple(results[: request.limit])

    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        source = await self._get_source(source_id, context)
        if not revision.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "knowledge revision must not be blank")
        now = datetime.now(UTC)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE data_knowledge_sources
                    SET revision = ?, status = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (revision, KnowledgeStatus.INDEXING.value, now.isoformat(), source_id),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to start knowledge reindex"
            ) from exc
        _ = source
        return await self.ingest_source(source_id, content, location, context)

    async def remove_source(self, source_id: str, context: DataAccessContext) -> None:
        await self._get_source(source_id, context)
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE data_knowledge_sources SET status = ?, updated_at = ? "
                    "WHERE source_id = ?",
                    (KnowledgeStatus.REMOVED.value, datetime.now(UTC).isoformat(), source_id),
                )
                connection.execute(
                    "DELETE FROM data_knowledge_indexes WHERE source_id = ?",
                    (source_id,),
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to remove knowledge source"
            ) from exc

    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        access = _compat_context(context)
        try:
            validate_id(source_ref, "knowledge_source")
            await self._get_source(source_ref, access)
            source_id = source_ref
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "knowledge source_ref must be a canonical knowledge_source ID",
            ) from exc
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            now = datetime.now(UTC)
            source = KnowledgeSource(
                source_id=source_ref,
                project_id=context.project_id,
                owner_ref=_actor_ref(context),
                created_by=_actor_ref(context),
                title=source_ref,
                revision="1",
                status=KnowledgeStatus.REGISTERED,
                created_at=now,
                updated_at=now,
            )
            await self.register_source(source, access)
            source_id = source.source_id
        document = await self.ingest_source(source_id, content, "content", access)
        return StoredObject(
            object_ref=document.document_id,
            metadata={"source_id": source_id, "revision": document.revision},
        )

    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        raw_source_ids = request.filters.get("source_ids")
        source_ids: tuple[str, ...] = ()
        if isinstance(raw_source_ids, list) and all(
            isinstance(item, str) for item in raw_source_ids
        ):
            source_ids = tuple(cast(list[str], raw_source_ids))
        results = await self.search(
            KnowledgeSearchRequest(
                query=request.query,
                context=_compat_context(request.context),
                source_ids=source_ids,
            )
        )
        return tuple(
            KnowledgeHit(
                ref=result.document_id,
                content=result.content,
                score=result.score,
                metadata={
                    "source_id": result.source_id,
                    "revision": result.revision,
                    "location": result.location,
                },
            )
            for result in results
        )

    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit:
        access = _compat_context(context)
        source = await self._get_source(source_ref, access)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM data_knowledge_documents
                    WHERE source_id = ? AND revision = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (source.source_id, source.revision),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to read knowledge document"
            ) from exc
        if row is None:
            raise _not_found("knowledge document", source_ref)
        return KnowledgeHit(
            ref=cast(str, row["document_id"]),
            content=cast(str, row["content"]),
            metadata={
                "source_id": source.source_id,
                "revision": source.revision,
                "location": cast(str, row["location"]),
            },
        )

    async def _get_source(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        validate_id(source_id, "knowledge_source")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_knowledge_sources WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read knowledge source") from exc
        if row is None:
            raise _not_found("knowledge source", source_id)
        source = self._source_from_row(row)
        self._check_project(source.project_id, context)
        return source

    async def _list_sources(self, context: DataAccessContext) -> tuple[KnowledgeSource, ...]:
        try:
            with self._connect() as connection:
                if context.project_id is None:
                    rows = connection.execute(
                        "SELECT * FROM data_knowledge_sources WHERE project_id IS NULL"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM data_knowledge_sources WHERE project_id = ?",
                        (context.project_id,),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to list knowledge sources"
            ) from exc
        return tuple(self._source_from_row(row) for row in rows)

    @staticmethod
    def _check_project(project_id: str | None, context: DataAccessContext) -> None:
        if project_id != context.project_id:
            raise _forbidden("knowledge source belongs to a different project/workspace")

    @staticmethod
    def _source_from_row(row: sqlite3.Row) -> KnowledgeSource:
        return KnowledgeSource(
            source_id=cast(str, row["source_id"]),
            project_id=cast(str | None, row["project_id"]),
            owner_ref=cast(str, row["owner_ref"]),
            created_by=cast(str, row["created_by"]),
            title=cast(str, row["title"]),
            revision=cast(str, row["revision"]),
            status=KnowledgeStatus(cast(str, row["status"])),
            created_at=_parse_time(cast(str, row["created_at"])),
            updated_at=_parse_time(cast(str, row["updated_at"])),
            content_checksum=cast(str | None, row["content_checksum"]),
            metadata=_json_dict(cast(str, row["metadata_json"])),
        )
