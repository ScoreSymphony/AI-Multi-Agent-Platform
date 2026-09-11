"""Local KnowledgeProvider reference implementation."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    KnowledgeHit,
    KnowledgeQuery,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.domain import validate_id

from .contracts import KnowledgeProvider
from .models import (
    DataAccessContext,
    IndexReference,
    KnowledgeDocument,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    KnowledgeStatus,
    SourceRef,
    new_knowledge_document_id,
    new_knowledge_index_id,
)
from .reference_support import SqliteReferenceStore as _SqliteMixin
from .reference_support import actor_ref as _actor_ref
from .reference_support import compat_context as _compat_context
from .reference_support import forbidden as _forbidden
from .reference_support import json_dict as _json_dict
from .reference_support import json_dump as _json_dump
from .reference_support import not_found as _not_found
from .reference_support import parse_time as _parse_time


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
