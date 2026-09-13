from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    new_memory_id,
)


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-892-memory-async",
            owner_type="user",
            owner_id="user-a",
        ),
        actor_ref="user:user-a",
    )


def _entry(*, value: str = "memory value") -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value=value,
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        memory_type=MemoryType.SEMANTIC,
    )


def test_memory_sqlite_runtime_keeps_event_loop_responsive_and_connections_in_workers(
    tmp_path: Path,
) -> None:
    class SlowMemoryProvider(LocalMemoryProvider):
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
        provider = SlowMemoryProvider(tmp_path / "responsive.sqlite3")
        operation = asyncio.create_task(
            provider.query_entries(
                MemoryQuery(MemoryScope.USER, "user-a"),
                _context(),
            )
        )
        heartbeat_count = 0
        while not operation.done():
            heartbeat_count += 1
            await asyncio.sleep(0.005)
        assert await operation == ()
        assert heartbeat_count >= 5
        assert provider.connect_threads
        assert all(thread_id != main_thread for thread_id in provider.connect_threads)

    asyncio.run(scenario())


def test_memory_sqlite_runtime_bounds_concurrent_worker_operations(tmp_path: Path) -> None:
    class MeasuringMemoryProvider(LocalMemoryProvider):
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
        provider = MeasuringMemoryProvider(tmp_path / "bounded.sqlite3")
        query = MemoryQuery(MemoryScope.USER, "user-a")
        await asyncio.gather(*(provider.query_entries(query, _context()) for _ in range(8)))
        assert provider.maximum_active == 2

    asyncio.run(scenario())


def test_memory_sqlite_runtime_defers_repeated_cancellation_until_write_settles(
    tmp_path: Path,
) -> None:
    started = threading.Event()
    release = threading.Event()

    class BlockingMemoryProvider(LocalMemoryProvider):
        @staticmethod
        def _insert_entry(connection: sqlite3.Connection, entry: MemoryEntry) -> None:
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("test memory write was not released")
            LocalMemoryProvider._insert_entry(connection, entry)

    async def scenario() -> None:
        db_path = tmp_path / "cancel.sqlite3"
        provider = BlockingMemoryProvider(db_path, max_concurrency=1)
        entry = _entry(value="cancelled caller")
        write = asyncio.create_task(provider.write_entry(entry, _context()))
        assert await asyncio.to_thread(started.wait, 1)

        write.cancel()
        write.cancel()
        await asyncio.sleep(0.02)
        assert not write.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await write

        restarted = LocalMemoryProvider(db_path)
        assert (await restarted.get_entry(entry.memory_id, _context())).value == "cancelled caller"

    asyncio.run(scenario())


def test_memory_sqlite_runtime_maps_busy_to_retryable_transient_failure(tmp_path: Path) -> None:
    class LockedMemoryProvider(LocalMemoryProvider):
        @staticmethod
        def _insert_entry(connection: sqlite3.Connection, entry: MemoryEntry) -> None:
            del connection, entry
            raise sqlite3.OperationalError("database is locked")

    async def scenario() -> None:
        provider = LockedMemoryProvider(tmp_path / "locked.sqlite3")
        with pytest.raises(ContractError) as failure:
            await provider.write_entry(_entry(), _context())
        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

    asyncio.run(scenario())


def test_memory_sqlite_runtime_rolls_back_failed_write(tmp_path: Path) -> None:
    class FailingMemoryProvider(LocalMemoryProvider):
        @staticmethod
        def _insert_entry(connection: sqlite3.Connection, entry: MemoryEntry) -> None:
            LocalMemoryProvider._insert_entry(connection, entry)
            raise sqlite3.OperationalError("injected write failure")

    async def scenario() -> None:
        db_path = tmp_path / "rollback.sqlite3"
        entry = _entry(value="must roll back")
        provider = FailingMemoryProvider(db_path)
        with pytest.raises(ContractError) as failure:
            await provider.write_entry(entry, _context())
        assert failure.value.code is ErrorCode.BACKEND_ERROR

        restarted = LocalMemoryProvider(db_path)
        with pytest.raises(ContractError) as missing:
            await restarted.get_entry(entry.memory_id, _context())
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_memory_sqlite_runtime_preserves_restart_semantics(tmp_path: Path) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "restart.sqlite3"
        entry = _entry(value="durable memory")
        first = LocalMemoryProvider(db_path)
        await first.write_entry(entry, _context())

        restarted = LocalMemoryProvider(db_path)
        restored = await restarted.get_entry(entry.memory_id, _context())
        assert restored == entry
        assert await restarted.query_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                memory_types=(MemoryType.SEMANTIC,),
            ),
            _context(),
        ) == (entry,)

    asyncio.run(scenario())
