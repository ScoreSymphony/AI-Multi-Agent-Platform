from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
)
from ai_multi_agent_platform.domain import new_id

from .test_knowledge_async_persistence import _context, _source


def test_knowledge_reindex_serializes_same_source_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_ingest_started = asyncio.Event()
        release_first_ingest = asyncio.Event()
        ingest_calls = 0

        class BlockingFirstIngestProvider(LocalKnowledgeProvider):
            async def ingest_source(
                self,
                source_id: str,
                content: str,
                location: str,
                context: DataAccessContext,
            ) -> KnowledgeDocument:
                nonlocal ingest_calls
                ingest_calls += 1
                if ingest_calls == 1:
                    first_ingest_started.set()
                    await release_first_ingest.wait()
                return await super().ingest_source(source_id, content, location, context)

        project_id = new_id("project")
        context = _context(project_id)
        provider = BlockingFirstIngestProvider(tmp_path / "serialized-reindex.sqlite3")
        source = _source(project_id)
        await provider.register_source(source, context)

        first = asyncio.create_task(
            provider.reindex_source(
                source.source_id,
                "r2",
                "content for revision two",
                "section:r2",
                context,
            )
        )
        await asyncio.wait_for(first_ingest_started.wait(), timeout=1)

        second = asyncio.create_task(
            provider.reindex_source(
                source.source_id,
                "r3",
                "content for revision three",
                "section:r3",
                context,
            )
        )
        await asyncio.sleep(0.05)

        in_progress = await provider.get_source(source.source_id, context)
        assert in_progress.revision == "r2"
        assert in_progress.status is KnowledgeStatus.INDEXING
        assert not second.done()

        release_first_ingest.set()
        first_document, second_document = await asyncio.gather(first, second)

        assert first_document.revision == "r2"
        assert first_document.content == "content for revision two"
        assert second_document.revision == "r3"
        assert second_document.content == "content for revision three"

        final_source = await provider.get_source(source.source_id, context)
        final_index = await provider.get_index_status(source.source_id, context)
        assert final_source.revision == "r3"
        assert final_source.status is KnowledgeStatus.READY
        assert final_index.revision == "r3"
        assert final_index.status is KnowledgeStatus.READY

    asyncio.run(scenario())


def test_knowledge_reindex_transient_ingest_failure_marks_requested_revision_failed(
    tmp_path: Path,
) -> None:
    class BusyDuringReindexProvider(LocalKnowledgeProvider):
        fail_indexing_read = True

        def _read_source(
            self,
            connection: sqlite3.Connection,
            source_id: str,
            context: DataAccessContext,
        ) -> KnowledgeSource:
            source = super()._read_source(connection, source_id, context)
            if self.fail_indexing_read and source.status is KnowledgeStatus.INDEXING:
                self.fail_indexing_read = False
                raise sqlite3.OperationalError("database is locked")
            return source

    async def scenario() -> None:
        project_id = new_id("project")
        context = _context(project_id)
        provider = BusyDuringReindexProvider(tmp_path / "transient-reindex.sqlite3")
        source = _source(project_id)
        await provider.register_source(source, context)

        with pytest.raises(ContractError) as failure:
            await provider.reindex_source(
                source.source_id,
                "r2",
                "content blocked by transient SQLite contention",
                "section:transient",
                context,
            )

        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

        failed_source = await provider.get_source(source.source_id, context)
        failed_index = await provider.get_index_status(source.source_id, context)
        assert failed_source.revision == "r2"
        assert failed_source.status is KnowledgeStatus.FAILED
        assert failed_index.revision == "r2"
        assert failed_index.status is KnowledgeStatus.FAILED

    asyncio.run(scenario())
