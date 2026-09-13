"""Async Accounting persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from pathlib import Path

import pytest

from ai_multi_agent_platform.accounting import (
    AccountingPersistenceOffload,
    AccountingService,
    AsyncAccountingServiceAdapter,
    InMemoryUsageStore,
    MeasurementQuality,
    SQLiteUsageStore,
    UsageQuery,
    UsageRecord,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode


def _record(source: str = "async-accounting") -> UsageRecord:
    return UsageRecord(
        metric_type="runtime.test.count",
        unit="count",
        quality=MeasurementQuality.MEASURED,
        source=source,
        quantity=1.0,
    )


class _RecordingUsageStore(SQLiteUsageStore):
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


class _BlockingUsageStore(SQLiteUsageStore):
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
                raise TimeoutError("test Accounting persistence release timed out")
        return super()._connect()


class _BusyUsageStore(SQLiteUsageStore):
    def __init__(self, path: Path) -> None:
        self.busy = False
        super().__init__(path)
        self.busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


class _FailingUsageStore(SQLiteUsageStore):
    def __init__(self, path: Path) -> None:
        self.fail = False
        super().__init__(path)
        self.fail = True

    def _connect(self) -> sqlite3.Connection:
        if self.fail:
            raise sqlite3.OperationalError("disk I/O error")
        return super()._connect()


def test_accounting_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        store = _RecordingUsageStore(tmp_path / "usage.sqlite3")
        adapter = AsyncAccountingServiceAdapter(AccountingService(store))
        event_loop_thread = threading.get_ident()
        before = len(store.connection_threads)

        write = asyncio.create_task(adapter.record(_record()))
        await asyncio.sleep(0.01)

        assert not write.done()
        default_worker = await asyncio.wait_for(
            asyncio.to_thread(threading.get_ident),
            timeout=0.5,
        )
        assert default_worker != event_loop_thread
        await write
        runtime_threads = store.connection_threads[before:]
        assert runtime_threads
        assert all(thread_id != event_loop_thread for thread_id in runtime_threads)

    asyncio.run(scenario())


def test_accounting_persistence_queue_is_bounded_before_executor_submission() -> None:
    async def scenario() -> None:
        offload = AccountingPersistenceOffload(max_concurrency=1, max_pending=1)
        started = threading.Event()
        release = threading.Event()

        def blocked() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Accounting worker release timed out")

        pending = asyncio.create_task(offload.run(blocked))
        assert await asyncio.to_thread(started.wait, 1)

        with pytest.raises(ContractError) as failure:
            await offload.run(lambda: None)
        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True

        release.set()
        await pending

    asyncio.run(scenario())


def test_independent_accounting_adapters_share_store_serialization(tmp_path: Path) -> None:
    store = SQLiteUsageStore(tmp_path / "usage.sqlite3")
    first = AsyncAccountingServiceAdapter(AccountingService(store))
    second = AsyncAccountingServiceAdapter(AccountingService(store))

    assert first.offload is second.offload


def test_shared_accounting_runtime_is_weakly_owned(tmp_path: Path) -> None:
    store = SQLiteUsageStore(tmp_path / "usage.sqlite3")
    accounting = AccountingService(store)
    adapter = AsyncAccountingServiceAdapter(accounting)
    store_ref = weakref.ref(store)
    offload_ref = weakref.ref(adapter.offload)

    del adapter
    del accounting
    del store
    gc.collect()

    assert store_ref() is None
    assert offload_ref() is None


def test_accounting_read_waits_for_inflight_write_and_restart_sees_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "usage.sqlite3"
        store = _BlockingUsageStore(database)
        writer = AsyncAccountingServiceAdapter(AccountingService(store))
        reader = AsyncAccountingServiceAdapter(AccountingService(store))
        record = _record("serialized")
        store.arm()

        write = asyncio.create_task(writer.record(record))
        assert await asyncio.to_thread(store.started.wait, 1)

        read = asyncio.create_task(reader.query(UsageQuery()))
        await asyncio.sleep(0.02)
        assert not read.done()

        store.release.set()
        await write
        listed = await read

        assert listed == (record,)
        restarted = SQLiteUsageStore(database)
        assert restarted.query(UsageQuery()) == (record,)

    asyncio.run(scenario())


def test_accounting_cancellation_waits_for_durable_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "usage.sqlite3"
        store = _BlockingUsageStore(database)
        adapter = AsyncAccountingServiceAdapter(AccountingService(store))
        record = _record("cancelled-caller")
        store.arm()

        pending = asyncio.create_task(adapter.record(record))
        assert await asyncio.to_thread(store.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        assert not pending.done()

        store.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SQLiteUsageStore(database)
        assert restarted.query(UsageQuery()) == (record,)

    asyncio.run(scenario())


def test_accounting_worker_failure_wins_over_pending_cancellation() -> None:
    async def scenario() -> None:
        offload = AccountingPersistenceOffload(max_concurrency=1, max_pending=1)
        started = threading.Event()
        release = threading.Event()

        def fail_after_release() -> None:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Accounting worker release timed out")
            raise RuntimeError("accounting persistence failed")

        pending = asyncio.create_task(offload.run(fail_after_release))
        assert await asyncio.to_thread(started.wait, 1)
        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        release.set()

        with pytest.raises(RuntimeError, match="accounting persistence failed"):
            await pending

    asyncio.run(scenario())


def test_accounting_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "usage.sqlite3"
        store = _BusyUsageStore(database)
        adapter = AsyncAccountingServiceAdapter(AccountingService(store))

        with pytest.raises(ContractError) as failure:
            await adapter.record(_record("busy"))

        assert failure.value.code is ErrorCode.TRANSIENT_FAILURE
        assert failure.value.retryable is True
        assert SQLiteUsageStore(database).query(UsageQuery()) == ()

    asyncio.run(scenario())


def test_accounting_sqlite_backend_error_maps_to_backend_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "usage.sqlite3"
        store = _FailingUsageStore(database)
        adapter = AsyncAccountingServiceAdapter(AccountingService(store))

        with pytest.raises(ContractError) as failure:
            await adapter.record(_record("backend-error"))

        assert failure.value.code is ErrorCode.BACKEND_ERROR
        assert failure.value.retryable is False
        assert SQLiteUsageStore(database).query(UsageQuery()) == ()

    asyncio.run(scenario())


def test_accounting_in_memory_and_sqlite_contracts_match(tmp_path: Path) -> None:
    async def scenario() -> None:
        record = _record("parity")
        memory = AsyncAccountingServiceAdapter(AccountingService(InMemoryUsageStore()))
        sqlite = AsyncAccountingServiceAdapter(
            AccountingService(SQLiteUsageStore(tmp_path / "usage.sqlite3"))
        )

        await memory.record(record)
        await sqlite.record(record)

        assert await memory.query(UsageQuery()) == (record,)
        assert await sqlite.query(UsageQuery()) == (record,)

    asyncio.run(scenario())
