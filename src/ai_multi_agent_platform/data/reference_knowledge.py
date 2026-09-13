"""Local KnowledgeProvider reference implementation."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
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

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class _KnowledgeSqliteOffload:
    """Bound blocking Knowledge SQLite work without using asyncio's shared executor."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="knowledge-sqlite",
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


class LocalKnowledgeProvider(_SqliteMixin, KnowledgeProvider):
    """SQLite source registry with non-blocking deterministic keyword retrieval."""

    def __init__(self, db_path: str | Path, *, max_concurrency: int = 4) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._knowledge_offload = _KnowledgeSqliteOffload(max_concurrency=max_concurrency)
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

    async def _run_knowledge_sqlite[T](
        self,
        operation: Callable[[], T],
        *,
        message: str,
        write: bool = False,
    ) -> T:
        try:
            return await self._knowledge_offload.run(operation, write=write)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise _map_knowledge_sqlite_error(exc, message) from exc

    async def _complete_knowledge_mutation[T](self, operation: Awaitable[T]) -> T:
        """Defer caller cancellation until a multi-step logical mutation has settled."""

        task = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            failure = task.exception()
            if failure is not None:
                raise failure from None
            raise

    async def register_source(
        self,
        source: KnowledgeSource,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        self._check_project(source.project_id, context)

        def operation() -> KnowledgeSource:
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
            return source

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to register knowledge source",
            write=True,
        )

    async def ingest_source(
        self,
        source_id: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        validate_id(source_id, "knowledge_source")

        def operation() -> KnowledgeDocument:
            with self._connect() as connection:
                source = self._read_source(connection, source_id, context)
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
            return document

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to ingest knowledge source",
            write=True,
        )

    async def get_index_status(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> IndexReference:
        validate_id(source_id, "knowledge_source")

        def operation() -> IndexReference:
            with self._connect() as connection:
                source = self._read_source(connection, source_id, context)
                if source.status is KnowledgeStatus.REMOVED:
                    raise _not_found("knowledge source", source_id)
                row = connection.execute(
                    "SELECT * FROM data_knowledge_indexes WHERE source_id = ?",
                    (source_id,),
                ).fetchone()
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

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to read knowledge index",
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
        terms = tuple(term.casefold() for term in request.query.split() if term.strip())
        if not terms:
            return ()

        def operation() -> tuple[KnowledgeSearchResult, ...]:
            with self._connect() as connection:
                if request.source_ids:
                    sources = tuple(
                        self._read_source(connection, source_id, request.context)
                        for source_id in request.source_ids
                    )
                else:
                    sources = self._list_sources_from_connection(connection, request.context)
                active = tuple(
                    source for source in sources if source.status is not KnowledgeStatus.REMOVED
                )
                if not active:
                    return ()
                source_by_id = {source.source_id: source for source in active}
                placeholders = ",".join("?" for _ in source_by_id)
                rows = connection.execute(
                    f"""
                    SELECT * FROM data_knowledge_documents
                    WHERE source_id IN ({placeholders})
                    ORDER BY created_at DESC
                    """,
                    tuple(source_by_id),
                ).fetchall()

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

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to search knowledge",
        )

    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        return await self._complete_knowledge_mutation(
            self._reindex_source_impl(source_id, revision, content, location, context)
        )

    async def _reindex_source_impl(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        validate_id(source_id, "knowledge_source")
        if not revision.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "knowledge revision must not be blank")

        def start_reindex() -> None:
            with self._connect() as connection:
                self._read_source(connection, source_id, context)
                connection.execute(
                    """
                    UPDATE data_knowledge_sources
                    SET revision = ?, status = ?, updated_at = ?
                    WHERE source_id = ?
                    """,
                    (
                        revision,
                        KnowledgeStatus.INDEXING.value,
                        datetime.now(UTC).isoformat(),
                        source_id,
                    ),
                )

        await self._run_knowledge_sqlite(
            start_reindex,
            message="failed to start knowledge reindex",
            write=True,
        )
        return await self.ingest_source(source_id, content, location, context)

    async def remove_source(self, source_id: str, context: DataAccessContext) -> None:
        validate_id(source_id, "knowledge_source")

        def operation() -> None:
            with self._connect() as connection:
                self._read_source(connection, source_id, context)
                connection.execute(
                    "UPDATE data_knowledge_sources SET status = ?, updated_at = ? "
                    "WHERE source_id = ?",
                    (KnowledgeStatus.REMOVED.value, datetime.now(UTC).isoformat(), source_id),
                )
                connection.execute(
                    "DELETE FROM data_knowledge_indexes WHERE source_id = ?",
                    (source_id,),
                )

        await self._run_knowledge_sqlite(
            operation,
            message="failed to remove knowledge source",
            write=True,
        )

    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        return await self._complete_knowledge_mutation(self._index_impl(source_ref, content, context))

    async def _index_impl(
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
        validate_id(source_ref, "knowledge_source")

        def operation() -> KnowledgeHit:
            with self._connect() as connection:
                source = self._read_source(connection, source_ref, access)
                row = connection.execute(
                    """
                    SELECT * FROM data_knowledge_documents
                    WHERE source_id = ? AND revision = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (source.source_id, source.revision),
                ).fetchone()
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

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to read knowledge document",
        )

    async def _get_source(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        validate_id(source_id, "knowledge_source")

        def operation() -> KnowledgeSource:
            with self._connect() as connection:
                return self._read_source(connection, source_id, context)

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to read knowledge source",
        )

    async def _list_sources(self, context: DataAccessContext) -> tuple[KnowledgeSource, ...]:
        def operation() -> tuple[KnowledgeSource, ...]:
            with self._connect() as connection:
                return self._list_sources_from_connection(connection, context)

        return await self._run_knowledge_sqlite(
            operation,
            message="failed to list knowledge sources",
        )

    def _read_source(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        row = connection.execute(
            "SELECT * FROM data_knowledge_sources WHERE source_id = ?",
            (source_id,),
        ).fetchone()
        if row is None:
            raise _not_found("knowledge source", source_id)
        source = self._source_from_row(row)
        self._check_project(source.project_id, context)
        return source

    def _list_sources_from_connection(
        self,
        connection: sqlite3.Connection,
        context: DataAccessContext,
    ) -> tuple[KnowledgeSource, ...]:
        if context.project_id is None:
            rows = connection.execute(
                "SELECT * FROM data_knowledge_sources WHERE project_id IS NULL"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM data_knowledge_sources WHERE project_id = ?",
                (context.project_id,),
            ).fetchall()
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


def _map_knowledge_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)
