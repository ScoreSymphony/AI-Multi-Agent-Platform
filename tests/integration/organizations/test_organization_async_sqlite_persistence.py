"""Async SQLite Organization persistence integration coverage for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.organizations import SqliteOrganizationRepository
from ai_multi_agent_platform.organizations._sqlite_async import AsyncSqliteOffload


class _SlowOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, path: str | Path) -> None:
        self.delay_connections = False
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.delay_connections:
            time.sleep(0.08)
        return super()._connect()


class _RecordingOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, path: str | Path) -> None:
        self.connection_threads: list[int] = []
        super().__init__(path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect()


class _ConcurrencyOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, path: str | Path, *, max_concurrency: int) -> None:
        self._counter_lock = threading.Lock()
        self.measure_connections = False
        self.active_reads = 0
        self.max_active_reads = 0
        super().__init__(path, max_concurrency=max_concurrency)
        self.measure_connections = True

    def _connect(self) -> sqlite3.Connection:
        if not self.measure_connections:
            return super()._connect()
        with self._counter_lock:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            time.sleep(0.04)
            return super()._connect()
        finally:
            with self._counter_lock:
                self.active_reads -= 1


class _BusyOrganizationRepository(SqliteOrganizationRepository):
    def __init__(self, path: str | Path) -> None:
        self.raise_busy = False
        super().__init__(path)
        self.raise_busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.raise_busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


def test_organization_sqlite_read_does_not_block_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _SlowOrganizationRepository(tmp_path / "organizations.sqlite3")
        pending = asyncio.create_task(repository.list_organizations())

        await asyncio.sleep(0.01)

        assert not pending.done()
        assert await pending == ()

    asyncio.run(scenario())


def test_organization_sqlite_connections_are_opened_in_worker_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _RecordingOrganizationRepository(tmp_path / "organizations.sqlite3")
        event_loop_thread = threading.get_ident()

        assert await repository.list_organizations() == ()

        assert repository.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in repository.connection_threads)

    asyncio.run(scenario())


def test_organization_sqlite_offload_concurrency_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _ConcurrencyOrganizationRepository(
            tmp_path / "organizations.sqlite3",
            max_concurrency=3,
        )

        results = await asyncio.gather(*(repository.list_organizations() for _ in range(12)))

        assert results == [()] * 12
        assert 1 < repository.max_active_reads <= 3

    asyncio.run(scenario())


def test_organization_sqlite_maps_busy_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BusyOrganizationRepository(tmp_path / "organizations.sqlite3")

        with pytest.raises(ContractError) as raised:
            await repository.list_organizations()

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_organization_sqlite_repeated_cancellation_waits_for_worker_boundary() -> None:
    async def scenario() -> None:
        offload = AsyncSqliteOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def operation() -> None:
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test operation release timed out")

        pending = asyncio.create_task(offload.run(operation, write=True))
        assert await asyncio.to_thread(started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
