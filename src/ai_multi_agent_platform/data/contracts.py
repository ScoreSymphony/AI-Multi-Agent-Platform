"""Refined replaceable contracts for platform data concerns."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts.interfaces import (
    FileProvider as CoreFileProvider,
)
from ai_multi_agent_platform.contracts.interfaces import (
    KnowledgeProvider as CoreKnowledgeProvider,
)
from ai_multi_agent_platform.contracts.interfaces import (
    MemoryProvider as CoreMemoryProvider,
)
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    DataAccessContext,
    FileRecord,
    IndexReference,
    KnowledgeDocument,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    MemoryEntry,
    MemoryQuery,
    OrphanReport,
)


class FileProvider(CoreFileProvider):
    """Canonical file/artifact storage contract, independent of path/object keys."""

    @abstractmethod
    async def create_file(
        self,
        data: bytes,
        context: DataAccessContext,
        *,
        file_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> FileRecord: ...

    @abstractmethod
    async def get_file(self, file_id: str, context: DataAccessContext) -> FileRecord: ...

    @abstractmethod
    async def list_files(self, context: DataAccessContext) -> tuple[FileRecord, ...]: ...

    @abstractmethod
    def stream_file(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]: ...

    @abstractmethod
    async def delete_file(self, file_id: str, context: DataAccessContext) -> FileRecord: ...

    @abstractmethod
    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool: ...

    @abstractmethod
    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord: ...

    @abstractmethod
    async def detect_orphans(self, context: DataAccessContext) -> OrphanReport: ...


class MemoryProvider(CoreMemoryProvider):
    """Explicitly scoped memory contract; never canonical task/run history."""

    @abstractmethod
    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry: ...

    @abstractmethod
    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry: ...

    @abstractmethod
    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]: ...

    @abstractmethod
    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]: ...

    @abstractmethod
    async def supersede_entry(
        self,
        memory_id: str,
        replacement: MemoryEntry,
        context: DataAccessContext,
    ) -> MemoryEntry: ...

    @abstractmethod
    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None: ...

    @abstractmethod
    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]: ...


class KnowledgeProvider(CoreKnowledgeProvider):
    """Source-backed knowledge lifecycle and retrieval contract."""

    @abstractmethod
    async def register_source(
        self,
        source: KnowledgeSource,
        context: DataAccessContext,
    ) -> KnowledgeSource: ...

    @abstractmethod
    async def ingest_source(
        self,
        source_id: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument: ...

    @abstractmethod
    async def get_index_status(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> IndexReference: ...

    @abstractmethod
    async def search(
        self,
        request: KnowledgeSearchRequest,
    ) -> tuple[KnowledgeSearchResult, ...]: ...

    @abstractmethod
    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument: ...

    @abstractmethod
    async def remove_source(self, source_id: str, context: DataAccessContext) -> None: ...
