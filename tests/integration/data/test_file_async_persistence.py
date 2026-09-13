from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.data._async_offload import AsyncDataOffload
from ai_multi_agent_platform.domain import new_id


def _context() -> DataAccessContext:
    project_id = new_id("project")
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-892-data-file",
            owner_type="user",
            owner_id="data-user",
            project_id=project_id,
        ),
        actor_ref="user:data-user",
    )


class _SlowCreateProvider(LocalFileProvider):
    def _create_file_sync(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        time.sleep(0.08)
        return super()._create_file_sync(*args, **kwargs)  # type: ignore[arg-type]


class _RecordingProvider(LocalFileProvider):
    def __init__(self, root: Path, db_path: Path) -> None:
        self.connection_threads: list[int] = []
        self.read_threads: list[int] = []
        super().__init__(root, db_path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect()

    def _read_sync(self, file_id: str, context: DataAccessContext) -> bytes:
        self.read_threads.append(threading.get_ident())
        return super()._read_sync(file_id, context)


class _BoundedReadProvider(LocalFileProvider):
    def __init__(self, root: Path, db_path: Path, *, max_concurrency: int) -> None:
        self._counter_lock = threading.Lock()
        self.measure_reads = False
        self.active_reads = 0
        self.max_active_reads = 0
        super().__init__(root, db_path, max_concurrency=max_concurrency)

    def _get_file_sync(self, file_id: str, context: DataAccessContext):  # type: ignore[no-untyped-def]
        if not self.measure_reads:
            return super()._get_file_sync(file_id, context)
        with self._counter_lock:
            self.active_reads += 1
            self.max_active_reads = max(self.max_active_reads, self.active_reads)
        try:
            time.sleep(0.04)
            return super()._get_file_sync(file_id, context)
        finally:
            with self._counter_lock:
                self.active_reads -= 1


class _BusyProvider(LocalFileProvider):
    def __init__(self, root: Path, db_path: Path) -> None:
        self.raise_busy = False
        super().__init__(root, db_path)
        self.raise_busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.raise_busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


def test_file_create_does_not_block_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = _SlowCreateProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        pending = asyncio.create_task(provider.create_file(b"payload", _context()))

        await asyncio.sleep(0.01)

        assert not pending.done()
        assert (await pending).size_bytes == 7

    asyncio.run(scenario())


def test_file_sqlite_and_bytes_runtime_work_use_worker_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        context = _context()
        provider = _RecordingProvider(tmp_path / "objects", tmp_path / "files.sqlite3")
        event_loop_thread = threading.get_ident()

        record = await provider.create_file(b"payload", context)
        assert await provider.read(record.file_id, context.operation) == b"payload"

        assert provider.connection_threads
        assert provider.read_threads
        assert all(thread_id != event_loop_thread for thread_id in provider.connection_threads)
        assert all(thread_id != event_loop_thread for thread_id in provider.read_threads)

    asyncio.run(scenario())


def test_file_runtime_concurrency_is_bounded(tmp_path: Path) -> None:
    async def scenario() -> None:
        context = _context()
        provider = _BoundedReadProvider(
            tmp_path / "objects",
            tmp_path / "files.sqlite3",
            max_concurrency=2,
        )
        record = await provider.create_file(b"payload", context)
        provider.measure_reads = True

        records = await asyncio.gather(
            *(provider.get_file(record.file_id, context) for _ in range(10))
        )

        assert records == [record] * 10
        assert 1 < provider.max_active_reads <= 2

    asyncio.run(scenario())


def test_file_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        provider = _BusyProvider(tmp_path / "objects", tmp_path / "files.sqlite3")

        with pytest.raises(ContractError) as raised:
            await provider.get_file(new_id("file"), _context())

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_file_state_and_bytes_survive_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        context = _context()
        root = tmp_path / "objects"
        database = tmp_path / "files.sqlite3"
        first = LocalFileProvider(root, database)
        record = await first.create_file(b"restart-safe", context)

        restarted = LocalFileProvider(root, database)
        assert await restarted.get_file(record.file_id, context) == record
        assert await restarted.read(record.file_id, context.operation) == b"restart-safe"
        assert await restarted.verify_checksum(record.file_id, context) is True

    asyncio.run(scenario())


def test_file_offload_repeated_cancellation_waits_for_worker_boundary() -> None:
    async def scenario() -> None:
        offload = AsyncDataOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def operation() -> None:
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test worker release timed out")

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
