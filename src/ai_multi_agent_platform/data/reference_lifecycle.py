"""Issue #251 lifecycle-capable local data providers.

These classes extend the issue-#13 SQLite reference implementations without changing
canonical identities or introducing a second persistence architecture. Existing SQLite
files are migrated in place when #251 metadata is first used.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    DataAccessContext,
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeStatus,
    MemoryEntry,
    MemoryOrigin,
    MemoryQuery,
    MemoryScope,
    MemoryType,
)
from .reference import LocalKnowledgeProvider as _BaseLocalKnowledgeProvider
from .reference import LocalMemoryProvider as _BaseLocalMemoryProvider


class LocalMemoryProvider(_BaseLocalMemoryProvider):
    """Local Memory provider with lifecycle metadata and canonical Memory Types."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path, max_concurrency=max_concurrency)
        added_operations = ("expire_entry", "list_entries_for_discovery")
        capabilities = tuple(
            replace(
                capability,
                supported_operations=tuple(
                    dict.fromkeys((*capability.supported_operations, *added_operations))
                ),
                features=tuple(
                    feature for feature in capability.features if feature != "six_scopes"
                )
                + (
                    "seven_scopes",
                    "memory_origin",
                    "memory_type_taxonomy",
                    "memory_type_filtering",
                    "exact_scoped_expiry",
                    "discovery_snapshot",
                ),
            )
            for capability in self._descriptor.capabilities
        )
        self._descriptor = replace(
            self._descriptor,
            supported_operations=tuple(
                dict.fromkeys((*self._descriptor.supported_operations, *added_operations))
            ),
            capabilities=capabilities,
        )

    def _initialize(self) -> None:
        super()._initialize()
        try:
            with self._connect() as connection:
                columns = {
                    cast(str, row["name"])
                    for row in connection.execute("PRAGMA table_info(data_memory)").fetchall()
                }
                if "origin" not in columns:
                    connection.execute(
                        "ALTER TABLE data_memory ADD COLUMN origin TEXT NOT NULL "
                        "DEFAULT 'user-authored'"
                    )
                if "memory_type" not in columns:
                    connection.execute(
                        "ALTER TABLE data_memory ADD COLUMN memory_type TEXT NOT NULL "
                        "DEFAULT 'unclassified'"
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS data_memory_scope_type_idx "
                    "ON data_memory(scope, scope_id, memory_type)"
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to migrate memory lifecycle metadata",
            ) from exc

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

        def dump(value: object) -> str:
            return json.dumps(value, sort_keys=True, separators=(",", ":"))

        connection.execute(
            """
            INSERT INTO data_memory (
                memory_id, scope, scope_id, owner_ref, created_by, value_json, created_at,
                retention, expires_at, provenance_json, supersedes_memory_id,
                superseded_by_memory_id, classification, metadata_json, origin,
                memory_type, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                entry.memory_id,
                entry.scope.value,
                entry.scope_id,
                entry.owner_ref,
                entry.created_by,
                dump(entry.value),
                entry.created_at.isoformat(),
                entry.retention.value,
                entry.expires_at.isoformat() if entry.expires_at is not None else None,
                dump(provenance),
                entry.supersedes_memory_id,
                entry.superseded_by_memory_id,
                entry.classification,
                dump(entry.metadata),
                entry.origin.value,
                entry.memory_type.value,
            ),
        )

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryEntry:
        entry = _BaseLocalMemoryProvider._memory_from_row(row)
        try:
            raw_origin = cast(str, row["origin"])
            origin = MemoryOrigin(raw_origin)
        except (KeyError, IndexError):
            origin = MemoryOrigin.USER_AUTHORED
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored memory origin is invalid",
            ) from exc
        try:
            raw_memory_type = cast(str, row["memory_type"])
            memory_type = MemoryType(raw_memory_type)
        except (KeyError, IndexError):
            memory_type = MemoryType.UNCLASSIFIED
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "stored memory type is invalid",
            ) from exc
        return replace(entry, origin=origin, memory_type=memory_type)

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        """Query one canonical scope with optional provider-neutral type filtering."""

        self._check_scope(query.scope, query.scope_id, context)
        clauses = ["scope = ?", "scope_id = ?", "deleted = 0"]
        parameters: list[object] = [query.scope.value, query.scope_id]
        if query.memory_types:
            placeholders = ",".join("?" for _ in query.memory_types)
            clauses.append(f"memory_type IN ({placeholders})")
            parameters.extend(memory_type.value for memory_type in query.memory_types)
        statement = (
            "SELECT * FROM data_memory WHERE " + " AND ".join(clauses) + " ORDER BY created_at DESC"
        )

        def operation() -> tuple[MemoryEntry, ...]:
            with self._connect() as connection:
                rows = connection.execute(statement, tuple(parameters)).fetchall()
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

    @staticmethod
    def _check_scope(scope: MemoryScope, scope_id: str, context: DataAccessContext) -> None:
        _BaseLocalMemoryProvider._check_scope(scope, scope_id, context)
        if scope is not MemoryScope.ORGANIZATION:
            return
        operation = context.operation
        if operation.owner_type == "organization" and operation.owner_id != scope_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "organization memory belongs to a different organization scope",
            )

    async def expire_entry(
        self,
        memory_id: str,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> MemoryEntry:
        """Tombstone exactly one due entry after canonical scope verification."""

        validate_id(memory_id, "memory")
        self._check_scope(query.scope, query.scope_id, context)

        def operation() -> MemoryEntry:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM data_memory WHERE memory_id = ? AND deleted = 0",
                    (memory_id,),
                ).fetchone()
                if row is None:
                    raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
                entry = self._memory_from_row(row)
                if entry.scope is not query.scope or entry.scope_id != query.scope_id:
                    raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
                if query.memory_types and entry.memory_type not in query.memory_types:
                    raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")
                self._check_scope(entry.scope, entry.scope_id, context)
                if entry.expires_at is None:
                    raise ContractError(
                        ErrorCode.INVALID_REQUEST,
                        "memory entry has no expiration time",
                    )
                if entry.expires_at > datetime.now(UTC):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "memory entry has not reached its expiration time",
                    )
                connection.execute(
                    "UPDATE data_memory SET deleted = 1 WHERE memory_id = ?",
                    (memory_id,),
                )
            return entry

        return await self._run_memory_sqlite(
            operation,
            message="failed to expire memory entry",
            write=True,
        )

    async def list_entries_for_discovery(self) -> tuple[MemoryEntry, ...]:
        """Return current canonical entries without exposing SQLite/provider identity."""

        def operation() -> tuple[MemoryEntry, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM data_memory WHERE deleted = 0 ORDER BY created_at, memory_id"
                ).fetchall()
            return tuple(self._memory_from_row(row) for row in rows)

        stored = await self._run_memory_sqlite(
            operation,
            message="failed to enumerate memory discovery snapshot",
        )
        now = datetime.now(UTC)
        return tuple(
            entry
            for entry in stored
            if (entry.expires_at is None or entry.expires_at > now)
            and entry.superseded_by_memory_id is None
        )


class LocalKnowledgeProvider(_BaseLocalKnowledgeProvider):
    """#13 local Knowledge provider with canonical #251 source management."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        super().__init__(db_path, max_concurrency=max_concurrency)
        added_operations = (
            "get_source",
            "list_sources",
            "update_source",
            "list_sources_for_discovery",
            "list_documents_for_discovery",
        )
        capabilities = tuple(
            replace(
                capability,
                supported_operations=tuple(
                    dict.fromkeys((*capability.supported_operations, *added_operations))
                ),
                features=tuple(
                    dict.fromkeys(
                        (
                            *capability.features,
                            "source_discovery",
                            "metadata_update",
                            "explicit_failure_state",
                            "discovery_snapshot",
                        )
                    )
                ),
            )
            for capability in self._descriptor.capabilities
        )
        self._descriptor = replace(
            self._descriptor,
            supported_operations=tuple(
                dict.fromkeys((*self._descriptor.supported_operations, *added_operations))
            ),
            capabilities=capabilities,
        )

    async def get_source(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        return await self._get_source(source_id, context)

    async def list_sources(
        self,
        context: DataAccessContext,
    ) -> tuple[KnowledgeSource, ...]:
        return await self._list_sources(context)

    async def list_sources_for_discovery(self) -> tuple[KnowledgeSource, ...]:
        def operation() -> tuple[KnowledgeSource, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM data_knowledge_sources ORDER BY created_at, source_id"
                ).fetchall()
            return tuple(self._source_from_row(row) for row in rows)

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to enumerate knowledge source discovery snapshot",
        )

    async def list_documents_for_discovery(self) -> tuple[KnowledgeDocument, ...]:
        def operation() -> tuple[KnowledgeDocument, ...]:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT * FROM data_knowledge_documents ORDER BY created_at, document_id"
                ).fetchall()
            return tuple(
                KnowledgeDocument(
                    document_id=cast(str, row["document_id"]),
                    source_id=cast(str, row["source_id"]),
                    revision=cast(str, row["revision"]),
                    content=cast(str, row["content"]),
                    location=cast(str, row["location"]),
                    checksum=cast(str, row["checksum"]),
                    created_at=datetime.fromisoformat(cast(str, row["created_at"])),
                )
                for row in rows
            )

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to enumerate knowledge document discovery snapshot",
        )

    async def update_source(
        self,
        source_id: str,
        context: DataAccessContext,
        *,
        title: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> KnowledgeSource:
        validate_id(source_id, "knowledge_source")

        def operation() -> KnowledgeSource:
            with self._connect() as connection:
                source = self._read_source(connection, source_id, context)
                updated = replace(
                    source,
                    title=source.title if title is None else title,
                    metadata=dict(source.metadata) if metadata is None else dict(metadata),
                    updated_at=datetime.now(UTC),
                )
                connection.execute(
                    """
                    UPDATE data_knowledge_sources
                    SET title = ?, updated_at = ?, metadata_json = ?
                    WHERE source_id = ?
                    """,
                    (
                        updated.title,
                        updated.updated_at.isoformat(),
                        json.dumps(updated.metadata, sort_keys=True, separators=(",", ":")),
                        source_id,
                    ),
                )
            return updated

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to update knowledge source metadata",
            write=True,
        )

    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        """Expose a durable FAILED state when re-indexing starts but ingestion fails."""

        return await self._complete_knowledge_mutation(
            self._reindex_with_failure_state(source_id, revision, content, location, context)
        )

    async def _reindex_with_failure_state(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        try:
            return await self._reindex_source_impl(source_id, revision, content, location, context)
        except ContractError as exc:
            if exc.code is ErrorCode.BACKEND_ERROR:
                await self._mark_reindex_failed(source_id, revision)
            raise

    async def _mark_reindex_failed(self, source_id: str, revision: str) -> None:
        """Best-effort failure checkpoint without masking the original provider error."""

        def operation() -> None:
            now = datetime.now(UTC).isoformat()
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE data_knowledge_sources
                    SET revision = ?, status = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (revision, KnowledgeStatus.FAILED.value, now, source_id),
                )
                connection.execute(
                    """
                    UPDATE data_knowledge_indexes
                    SET revision = ?, status = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (revision, KnowledgeStatus.FAILED.value, now, source_id),
                )

        try:
            await self._run_knowledge_sqlite(
                operation,
                message="failed to persist knowledge reindex failure state",
                write=True,
            )
        except ContractError:
            # Preserve the original backend failure. A completely unavailable metadata
            # store cannot be made healthier by replacing it with a secondary error.
            return
