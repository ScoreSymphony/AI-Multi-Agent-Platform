"""Async SQLite kernel repository integration coverage for event-loop safety."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, SqliteKernelRepository
from ai_multi_agent_platform.kernel.repository import EventRepository


def _event(stream_id: str, *, event_id: str | None = None) -> PlatformEvent:
    return PlatformEvent(
        id=event_id or new_id("event"),
        event_type="test.sqlite.commit",
        subject_type="task",
        subject_id=stream_id,
        correlation_id=stream_id,
        payload={"source": "issue-892"},
    )


class _SlowReadRepository(SqliteKernelRepository):
    def _read_events_sync(self, stream_id: str) -> tuple[PlatformEvent, ...]:
        time.sleep(0.08)
        return super()._read_events_sync(stream_id)


class _SlowCommitRepository(SqliteKernelRepository):
    def _commit_sync(self, **kwargs: Any) -> Any:
        time.sleep(0.08)
        return super()._commit_sync(**kwargs)


class _BusyRepository(SqliteKernelRepository):
    def _revision_sync(self, stream_id: str) -> int:
        del stream_id
        raise sqlite3.OperationalError("database is locked")


class _RecordingRepository(SqliteKernelRepository):
    def __init__(self, path: str | Path) -> None:
        self.connection_threads: list[int] = []
        super().__init__(path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect()


class _ConcurrencyRepository(SqliteKernelRepository):
    def __init__(self, path: str | Path) -> None:
        self._counter_lock = threading.Lock()
        self.active_reads = 0
        self.max_active_reads = 0
        super().__init__(path)

    def _revision_sync(self, stream_id: str) -> int:
        with self._counter_lock:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            time.sleep(0.04)
            return super()._revision_sync(stream_id)
        finally:
            with self._counter_lock:
                self.active_reads -= 1


def test_sqlite_kernel_read_does_not_block_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _SlowReadRepository(tmp_path / "kernel.sqlite3")
        stream_id = new_id("task")
        pending = asyncio.create_task(repository.read_events(stream_id))

        await asyncio.sleep(0.01)

        assert not pending.done()
        assert await pending == ()

    asyncio.run(scenario())


def test_sqlite_kernel_cancellation_waits_for_transaction_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "kernel.sqlite3"
        repository = _SlowCommitRepository(database)
        stream_id = new_id("task")
        event = _event(stream_id)

        pending = asyncio.create_task(
            repository.commit(
                stream_id=stream_id,
                expected_revision=0,
                events=(event,),
            )
        )
        await asyncio.sleep(0.01)
        pending.cancel()

        with pytest.raises(asyncio.CancelledError):
            await pending

        recovered = SqliteKernelRepository(database)
        assert await recovered.revision(stream_id) == 1
        assert await recovered.read_events(stream_id) == (event,)

    asyncio.run(scenario())


def test_sqlite_kernel_maps_busy_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BusyRepository(tmp_path / "kernel.sqlite3")

        with pytest.raises(ContractError) as raised:
            await repository.revision(new_id("task"))

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_sqlite_kernel_connections_are_opened_in_worker_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingRepository(tmp_path / "kernel.sqlite3")
        event_loop_thread = threading.get_ident()

        assert await repository.revision(new_id("task")) == 0

        assert repository.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in repository.connection_threads)

    asyncio.run(scenario())


def test_sqlite_kernel_offload_concurrency_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _ConcurrencyRepository(tmp_path / "kernel.sqlite3")
        streams = tuple(new_id("task") for _ in range(12))

        revisions = await asyncio.gather(*(repository.revision(stream_id) for stream_id in streams))

        assert revisions == [0] * len(streams)
        assert 1 < repository.max_active_reads <= 4

    asyncio.run(scenario())


def test_sqlite_kernel_failed_mutation_rolls_back(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = SqliteKernelRepository(tmp_path / "kernel.sqlite3")
        first_stream = new_id("task")
        second_stream = new_id("task")
        duplicate_id = new_id("event")

        await repository.commit(
            stream_id=first_stream,
            expected_revision=0,
            events=(_event(first_stream, event_id=duplicate_id),),
        )
        with pytest.raises(ContractError) as raised:
            await repository.commit(
                stream_id=second_stream,
                expected_revision=0,
                events=(_event(second_stream, event_id=duplicate_id),),
            )

        assert raised.value.code is ErrorCode.CONFLICT
        assert await repository.revision(first_stream) == 1
        assert await repository.revision(second_stream) == 0

    asyncio.run(scenario())


async def _assert_stale_revision_conflict(repository: EventRepository) -> None:
    stream_id = new_id("task")
    await repository.commit(
        stream_id=stream_id,
        expected_revision=0,
        events=(_event(stream_id),),
    )

    with pytest.raises(ContractError) as raised:
        await repository.commit(
            stream_id=stream_id,
            expected_revision=0,
            events=(_event(stream_id),),
        )

    assert raised.value.code is ErrorCode.CONFLICT
    assert raised.value.retryable is True
    assert raised.value.details == {
        "reason": "stale_stream_revision",
        "expected_revision": 0,
        "actual_revision": 1,
    }


def test_in_memory_and_sqlite_report_same_retryable_stale_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        await _assert_stale_revision_conflict(InMemoryKernelRepository())
        await _assert_stale_revision_conflict(SqliteKernelRepository(tmp_path / "stale.sqlite3"))

    asyncio.run(scenario())


async def _assert_repository_contract(repository: EventRepository) -> None:
    stream_id = new_id("task")
    event = _event(stream_id)

    assert await repository.revision(stream_id) == 0
    assert await repository.read_events(stream_id) == ()
    result = await repository.commit(
        stream_id=stream_id,
        expected_revision=0,
        events=(event,),
    )
    assert result.applied is True
    assert result.revision == 1
    assert await repository.revision(stream_id) == 1
    assert await repository.read_events(stream_id) == (event,)
    assert stream_id in await repository.list_stream_ids()


def test_in_memory_and_sqlite_kernel_repositories_share_async_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        await _assert_repository_contract(InMemoryKernelRepository())
        await _assert_repository_contract(SqliteKernelRepository(tmp_path / "kernel.sqlite3"))

    asyncio.run(scenario())
