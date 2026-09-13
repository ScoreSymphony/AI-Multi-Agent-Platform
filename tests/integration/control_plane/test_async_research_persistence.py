"""Async Research persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.research import (
    AsyncResearchRepositoryAdapter,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchItem,
    ResearchPersistenceOffload,
    SqliteResearchRepository,
)

OWNER = OwnerRef(type="user", id="issue-892-research")


def _item(title: str = "Async Research") -> ResearchItem:
    return ResearchItem(
        title=title,
        question="Does Research persistence stay off the event loop?",
        research_class=ResearchClass.PROJECT_RESEARCH,
        owner_ref=OWNER,
    )


class _RecordingResearchRepository(SqliteResearchRepository):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


class _BlockingResearchRepository(SqliteResearchRepository):
    def __init__(self, path: Path) -> None:
        self.block_connections = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def arm(self) -> None:
        self.started.clear()
        self.release.clear()
        self.block_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.block_connections:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test Research persistence release timed out")
        return super()._connect()


class _BusyResearchRepository(SqliteResearchRepository):
    def __init__(self, path: Path) -> None:
        self.busy = False
        super().__init__(path)
        self.busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


class _FailingResearchRepository(SqliteResearchRepository):
    def __init__(self, path: Path) -> None:
        self.fail = False
        super().__init__(path)
        self.fail = True

    def _connect(self) -> sqlite3.Connection:
        if self.fail:
            raise sqlite3.OperationalError("disk I/O error")
        return super()._connect()


def test_research_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingResearchRepository(tmp_path / "research.sqlite3")
        adapter = AsyncResearchRepositoryAdapter(repository)
        event_loop_thread = threading.get_ident()
        before = len(repository.connection_threads)

        write = asyncio.create_task(adapter.create_item(_item()))
        await asyncio.sleep(0.01)

        assert not write.done()
        stored = await write
        assert stored.title == "Async Research"
        runtime_threads = repository.connection_threads[before:]
        assert runtime_threads
        assert all(thread_id != event_loop_thread for thread_id in runtime_threads)

    asyncio.run(scenario())


def test_research_persistence_uses_dedicated_bounded_executor() -> None:
    async def scenario() -> None:
        offload = ResearchPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def blocked() -> int:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Research worker release timed out")
            return threading.get_ident()

        pending = [asyncio.create_task(offload.run(blocked)) for _ in range(8)]
        assert await asyncio.to_thread(started.wait, 1)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(threading.get_ident),
                timeout=0.5,
            )
            assert default_worker != threading.get_ident()
            assert any(not task.done() for task in pending)
        finally:
            release.set()

        worker_threads = await asyncio.gather(*pending)
        assert len(set(worker_threads)) == 1

    asyncio.run(scenario())


def test_independent_research_adapters_share_serialization(tmp_path: Path) -> None:
    repository = SqliteResearchRepository(tmp_path / "research.sqlite3")
    first = AsyncResearchRepositoryAdapter(repository)
    second = AsyncResearchRepositoryAdapter(repository)

    assert first.offload is second.offload


def test_shared_research_runtime_is_weakly_owned(tmp_path: Path) -> None:
    repository = SqliteResearchRepository(tmp_path / "research.sqlite3")
    adapter = AsyncResearchRepositoryAdapter(repository)
    repository_ref = weakref.ref(repository)
    offload_ref = weakref.ref(adapter.offload)

    del adapter
    del repository
    gc.collect()

    assert repository_ref() is None
    assert offload_ref() is None


def test_research_read_waits_for_inflight_write_and_restart_sees_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "research.sqlite3"
        repository = _BlockingResearchRepository(database)
        writer = AsyncResearchRepositoryAdapter(repository)
        reader = AsyncResearchRepositoryAdapter(repository)
        item = _item("Serialized Research")
        repository.arm()

        write = asyncio.create_task(writer.create_item(item))
        assert await asyncio.to_thread(repository.started.wait, 1)

        read = asyncio.create_task(reader.list_items())
        await asyncio.sleep(0.02)
        assert not read.done()

        repository.release.set()
        stored = await write
        listed = await read

        assert listed == (stored,)
        restarted = SqliteResearchRepository(database)
        assert restarted.get_item(item.research_item_id) == stored

    asyncio.run(scenario())


def test_research_cancellation_waits_for_durable_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "research.sqlite3"
        repository = _BlockingResearchRepository(database)
        adapter = AsyncResearchRepositoryAdapter(repository)
        item = _item("Cancellation Research")
        repository.arm()

        pending = asyncio.create_task(adapter.create_item(item))
        assert await asyncio.to_thread(repository.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        assert not pending.done()

        repository.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SqliteResearchRepository(database)
        assert restarted.get_item(item.research_item_id) == item

    asyncio.run(scenario())


def test_research_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        offload = ResearchPersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def fail_after_release() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Research worker release timed out")
            raise RuntimeError("research persistence failed")

        pending = asyncio.create_task(offload.run(fail_after_release))
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        release.set()

        with pytest.raises(RuntimeError, match="research persistence failed"):
            await pending

    asyncio.run(scenario())


def test_research_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "research.sqlite3"
        repository = _BusyResearchRepository(database)
        adapter = AsyncResearchRepositoryAdapter(repository)
        item = _item("Busy Research")

        with pytest.raises(ContractError) as failure:
            await adapter.create_item(item)

        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True
        assert repository.list_items() == ()
        assert SqliteResearchRepository(database).list_items() == ()

    asyncio.run(scenario())


def test_research_sqlite_backend_failure_rolls_back_memory_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "research.sqlite3"
        repository = _FailingResearchRepository(database)
        adapter = AsyncResearchRepositoryAdapter(repository)
        item = _item("Failed Research")

        with pytest.raises(ContractError) as failure:
            await adapter.create_item(item)

        assert failure.value.code is ErrorCode.BACKEND_ERROR
        assert failure.value.retryable is False
        assert repository.list_items() == ()
        assert SqliteResearchRepository(database).list_items() == ()

    asyncio.run(scenario())


def test_research_in_memory_and_sqlite_contracts_match(tmp_path: Path) -> None:
    async def scenario() -> None:
        item = _item("Parity Research")
        memory = AsyncResearchRepositoryAdapter(InMemoryResearchRepository())
        sqlite = AsyncResearchRepositoryAdapter(
            SqliteResearchRepository(tmp_path / "research.sqlite3")
        )

        memory_stored = await memory.create_item(item)
        sqlite_stored = await sqlite.create_item(item)

        assert memory_stored == sqlite_stored == item
        assert await memory.get_item(item.research_item_id) == item
        assert await sqlite.get_item(item.research_item_id) == item
        assert await memory.list_items() == await sqlite.list_items() == (item,)

    asyncio.run(scenario())
