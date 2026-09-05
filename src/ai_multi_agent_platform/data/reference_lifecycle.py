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
)
from .reference import LocalKnowledgeProvider as _BaseLocalKnowledgeProvider
from .reference import LocalMemoryProvider as _BaseLocalMemoryProvider


class LocalMemoryProvider(_BaseLocalMemoryProvider):
    """#13 local Memory provider with #251 origin persistence and Organization scope."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        capabilities = tuple(
            replace(
                capability,
                supported_operations=tuple(
                    dict.fromkeys((*capability.supported_operations, "expire_entry"))
                ),
                features=tuple(
                    feature for feature in capability.features if feature != "six_scopes"
                )
                + ("seven_scopes", "memory_origin", "exact_scoped_expiry"),
            )
            for capability in self._descriptor.capabilities
        )
        self._descriptor = replace(
            self._descriptor,
            supported_operations=tuple(
                dict.fromkeys((*self._descriptor.supported_operations, "expire_entry"))
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
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to migrate memory origin metadata",
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
                superseded_by_memory_id, classification, metadata_json, origin, deleted
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
        return replace(entry, origin=origin)

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
        try:
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
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to expire memory entry",
            ) from exc
        return entry


class LocalKnowledgeProvider(_BaseLocalKnowledgeProvider):
    """#13 local Knowledge provider with canonical #251 source management."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        added_operations = ("get_source", "list_sources", "update_source")
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

    async def update_source(
        self,
        source_id: str,
        context: DataAccessContext,
        *,
        title: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> KnowledgeSource:
        source = await self._get_source(source_id, context)
        updated = replace(
            source,
            title=source.title if title is None else title,
            metadata=dict(source.metadata) if metadata is None else dict(metadata),
            updated_at=datetime.now(UTC),
        )
        try:
            with self._connect() as connection:
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
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "failed to update knowledge source metadata",
            ) from exc
        return updated

    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        """Expose a durable FAILED state when re-indexing starts but ingestion fails."""

        try:
            return await super().reindex_source(source_id, revision, content, location, context)
        except ContractError as exc:
            if exc.code is ErrorCode.BACKEND_ERROR:
                self._mark_reindex_failed(source_id, revision)
            raise

    def _mark_reindex_failed(self, source_id: str, revision: str) -> None:
        """Best-effort failure checkpoint without masking the original provider error."""

        now = datetime.now(UTC).isoformat()
        try:
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
        except sqlite3.Error:
            # Preserve the original backend failure. A completely unavailable metadata
            # store cannot be made healthier by replacing it with a secondary error.
            return
