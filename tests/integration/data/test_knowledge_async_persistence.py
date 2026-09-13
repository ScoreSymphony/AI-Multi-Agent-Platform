from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSearchRequest,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import new_id


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-892-knowledge-async",
            project_id=project_id,
            owner_type="user",
            owner_id="user-a",
        ),
        actor_ref="user:user-a",
    )


def _source(project_id: str, *, title: str = "Knowledge source") -> KnowledgeSource:
    now = datetime.now(UTC)
    return KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title=title,
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )


def test_knowledge_sqlite_runtime_keeps_event_loop_responsive_and_connections_in_workers(
    tmp_path: Path,
) -> None:
    class SlowKnowledgeProvider(LocalKnowledgeProvider):
        def __init__(self, db_path: Path) -> None:
            self.track_runtime = False
            self.connect_threads: list[int] = []
            super().__init__(db_path)
            self.track_runtime = True

        def _connect(self) -> sqlite3.Connection:
            if self.track_runtime:
                self.connect_threads.append(threading.get_ident())
                time.sleep(0.08)
            return super()._connect()

    async def scenario() -> None:
        main_thread = threading.get_ident()
        project_id = new_id("project")
        provider = SlowKnowledgeProvider(tmp_path / "responsive.sqlite3")
        operation = asyncio.create_task(provider.list_sources(_context(project_id)))
        heartbeat_count = 0
        while not operation.done():
            heartbeat_count += 1
            await asyncio.sleep(0.005)
        assert await operation == ()
        assert heartbeat_count >= 5
        assert provider.connect_threads
        assert all(thread_id != main_thread for thread_id in provider.connect_threads)

    asyncio.run(scenario())


def test_knowledge_sqlite_runtime_bounds_concurrent_worker_operations(tmp_path: Path) -> None:
    class MeasuringKnowledgeProvider(LocalKnowledgeProvider):
        def __init__(self, db_path: Path) -> None:
            self.track_runtime = False
            self.measurement_lock = threading.Lock()
            self.active = 0
            self.maximum_active = 0
            super().__init__(db_path, max_concurrency=2)
            self.track_runtime = True

        def _connect(self) -> sqlite3.Connection:
            if self.track_runtime:
                with self.measurement_lock:
                    self.active += 1
                    self.maximum_active = max(self.maximum_active, self.active)
                try:
                    time.sleep(0.05)
                    return super()._connect()
                finally:
                    with self.measurement_lock:
                        self.active -= 1
            return super()._connect()

    async def scenario() -> None:
        project_id = new_id("project")
        provider = MeasuringKnowledgeProvider(tmp_path / "bounded.sqlite3")
        context = _context(project_id)
        await asyncio.gather(*(provider.list_sources(context) for _ in range(8)))
        assert provider.maximum_active == 2

    asyncio.run(scenario())


def test_knowledge_backlog_does_not_consume_shared_default_executor(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingKnowledgeProvider(LocalKnowledgeProvider):
        def __init__(self, db_path: Path) -> None:
            self.block_runtime = False
            super().__init__(db_path, max_concurrency=2)
            self.block_runtime = True

        def _connect(self) -> sqlite3.Connection:
            if self.block_runtime:
                started.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test Knowledge backlog was not released")
            return super()._connect()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-default")
        )
        project_id = new_id("project")
        context = _context(project_id)
        provider = BlockingKnowledgeProvider(tmp_path / "executor-isolation.sqlite3")
        writes = [
            asyncio.create_task(
                provider.register_source(_source(project_id, title=f"queued-{index}"), context)
            )
            for index in range(8)
        ]
        try:
            for _ in range(100):
                if started.is_set():
                    break
                await asyncio.sleep(0.005)
            assert started.is_set()
            unrelated = asyncio.create_task(asyncio.to_thread(lambda: "unrelated-ready"))
            assert await asyncio.wait_for(unrelated, timeout=0.5) == "unrelated-ready"
        finally:
            release.set()
            outcomes = await asyncio.gather(*writes, return_exceptions=True)
        assert all(isinstance(outcome, KnowledgeSource) for outcome in outcomes)

    asyncio.run(scenario())


def test_knowledge_reindex_defers_repeated_cancellation_until_ready(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingReindexProvider(LocalKnowledgeProvider):
        def _read_source(
            self,
            connection: sqlite3.Connection,
            source_id: str,
            context: DataAccessContext,
        ) -> KnowledgeSource:
            source = super()._read_source(connection, source_id, context)
            if source.status is KnowledgeStatus.INDEXING:
                started.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test Knowledge reindex was not released")
            return source

    async def scenario() -> None:
        db_path = tmp_path / "cancel-reindex.sqlite3"
        project_id = new_id("project")
        context = _context(project_id)
        provider = BlockingReindexProvider(db_path, max_concurrency=2)
        source = _source(project_id)
        await provider.register_source(source, context)

        reindex = asyncio.create_task(
            provider.reindex_source(
                source.source_id,
                "r2",
                "cancel-safe reindex content",
                "section:cancel",
                context,
            )
        )
        assert await asyncio.to_thread(started.wait, 1)
        reindex.cancel()
        reindex.cancel()
        await asyncio.sleep(0.02)
        assert not reindex.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await reindex

        restarted = LocalKnowledgeProvider(db_path)
        restored_source = await restarted.get_source(source.source_id, context)
        restored_index = await restarted.get_index_status(source.source_id, context)
        assert restored_source.revision == "r2"
        assert restored_source.status is KnowledgeStatus.READY
        assert restored_index.revision == "r2"
        assert restored_index.status is KnowledgeStatus.READY

    asyncio.run(scenario())


def test_knowledge_sqlite_runtime_maps_busy_to_retryable_transient_failure(tmp_path: Path) -> None:
    class LockedKnowledgeProvider(LocalKnowledgeProvider):
        def _read_source(
            self,
            connection: sqlite3.Connection,
            source_id: str,
            context: DataAccessContext,
        ) -> KnowledgeSource:
            del connection, source_id, context
            raise sqlite3.OperationalError("database is locked")

    async def scenario() -> None:
        project_id = new_id("project")
        provider = LockedKnowledgeProvider(tmp_path / "locked.sqlite3")
        with pytest.raises(ContractError) as failure:
            await provider.get_source(new_knowledge_source_id(), _context(project_id))
        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

    asyncio.run(scenario())


def test_knowledge_ingest_rolls_back_document_when_ready_transition_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "rollback.sqlite3"
        project_id = new_id("project")
        context = _context(project_id)
        provider = LocalKnowledgeProvider(db_path)
        source = _source(project_id)
        await provider.register_source(source, context)

        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_knowledge_ready
                BEFORE UPDATE OF status ON data_knowledge_sources
                WHEN NEW.status = 'ready'
                BEGIN
                    SELECT RAISE(ABORT, 'injected ready transition failure');
                END
                """
            )

        with pytest.raises(ContractError) as failure:
            await provider.ingest_source(
                source.source_id,
                "must roll back",
                "section:rollback",
                context,
            )
        assert failure.value.code is ErrorCode.BACKEND_ERROR

        with sqlite3.connect(db_path) as connection:
            documents = connection.execute(
                "SELECT COUNT(*) FROM data_knowledge_documents WHERE source_id = ?",
                (source.source_id,),
            ).fetchone()
        assert documents is not None
        assert documents[0] == 0
        restored = await provider.get_source(source.source_id, context)
        assert restored.status is KnowledgeStatus.REGISTERED
        assert restored.content_checksum is None

    asyncio.run(scenario())


def test_knowledge_sqlite_runtime_preserves_restart_and_search_semantics(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "restart.sqlite3"
        project_id = new_id("project")
        context = _context(project_id)
        source = _source(project_id, title="Durable knowledge")
        first = LocalKnowledgeProvider(db_path)
        await first.register_source(source, context)
        document = await first.ingest_source(
            source.source_id,
            "durable searchable knowledge",
            "section:restart",
            context,
        )

        restarted = LocalKnowledgeProvider(db_path)
        restored_source = await restarted.get_source(source.source_id, context)
        restored_index = await restarted.get_index_status(source.source_id, context)
        results = await restarted.search(
            KnowledgeSearchRequest(
                query="searchable",
                context=context,
                source_ids=(source.source_id,),
            )
        )
        assert restored_source.status is KnowledgeStatus.READY
        assert restored_source.content_checksum == document.checksum
        assert restored_index.status is KnowledgeStatus.READY
        assert len(results) == 1
        assert results[0].document_id == document.document_id
        assert results[0].citation.checksum == document.checksum

    asyncio.run(scenario())
