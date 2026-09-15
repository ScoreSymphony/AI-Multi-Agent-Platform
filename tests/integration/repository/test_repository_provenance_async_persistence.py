from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.context.source_adapters import RepositoryContextSourceAdapter
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.repositories.async_provenance import (
    AsyncRepositoryProvenanceAdapter,
    RepositoryProvenancePersistenceOffload,
    as_async_repository_provenance_store,
)
from ai_multi_agent_platform.repositories.models import RepositoryRunProvenance
from ai_multi_agent_platform.repositories.persistence import SqliteRepositoryProvenanceStore
from ai_multi_agent_platform.repositories.service import RepositoryProvenanceStore


class _SlowProvenanceStore(RepositoryProvenanceStore):
    def __init__(self) -> None:
        super().__init__()
        self._active_lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.thread_names: list[str] = []

    def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        with self._active_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.thread_names.append(threading.current_thread().name)
        try:
            time.sleep(0.03)
            return super().for_run(run_id)
        finally:
            with self._active_lock:
                self.active -= 1


class _ThreadRecordingSqliteStore(SqliteRepositoryProvenanceStore):
    def __init__(self, path: Path) -> None:
        self.connection_threads: list[tuple[str, int]] = []
        super().__init__(path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(
            (threading.current_thread().name, threading.get_ident())
        )
        return super()._connect()


class _FailingSqliteStore(SqliteRepositoryProvenanceStore):
    def __init__(self, path: Path) -> None:
        self.failure: sqlite3.Error | None = None
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        if self.failure is not None:
            raise self.failure
        return super()._connect()


class _NativeAsyncProvenanceStore:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], RepositoryRunProvenance] = {}

    async def record(self, provenance: RepositoryRunProvenance) -> None:
        self.records.setdefault((provenance.run_id, provenance.repository_id), provenance)

    async def upsert(self, provenance: RepositoryRunProvenance) -> None:
        self.records[(provenance.run_id, provenance.repository_id)] = provenance

    async def get(
        self,
        run_id: str,
        repository_id: str,
    ) -> RepositoryRunProvenance | None:
        return self.records.get((run_id, repository_id))

    async def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        return tuple(record for (current, _), record in self.records.items() if current == run_id)


def _record() -> RepositoryRunProvenance:
    return RepositoryRunProvenance(
        run_id=new_id("run"),
        repository_id=new_id("external_resource"),
        input_revision="a" * 40,
        actor_ref="service:repository-provenance-test",
        task_id=new_id("task"),
    )


def test_sqlite_provenance_runs_on_dedicated_worker_not_event_loop(tmp_path: Path) -> None:
    store = _ThreadRecordingSqliteStore(tmp_path / "repository-provenance.sqlite3")
    adapter = AsyncRepositoryProvenanceAdapter(store)
    record = _record()

    async def scenario() -> int:
        event_loop_thread = threading.get_ident()
        await adapter.upsert(record)
        assert await adapter.get(record.run_id, record.repository_id) == record
        return event_loop_thread

    event_loop_thread = asyncio.run(scenario())

    assert store.connection_threads
    assert all(
        name.startswith("repository-provenance-persistence")
        for name, _ in store.connection_threads
    )
    assert all(thread_id != event_loop_thread for _, thread_id in store.connection_threads)


def test_provenance_adapters_share_serialization_for_same_store() -> None:
    store = _SlowProvenanceStore()
    first = AsyncRepositoryProvenanceAdapter(store)
    second = AsyncRepositoryProvenanceAdapter(store)

    async def scenario() -> None:
        await asyncio.gather(first.for_run("run_one"), second.for_run("run_two"))

    asyncio.run(scenario())

    assert store.max_active == 1
    assert store.thread_names
    assert all(name.startswith("repository-provenance-persistence") for name in store.thread_names)


def test_provenance_persistence_queue_is_bounded_and_loop_remains_responsive() -> None:
    offload = RepositoryProvenancePersistenceOffload(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()
    heartbeat = asyncio.Event()

    def blocking_operation() -> str:
        started.set()
        release.wait(timeout=2)
        return threading.current_thread().name

    async def scenario() -> None:
        first = asyncio.create_task(offload.run(blocking_operation, message="first"))
        while not started.is_set():
            await asyncio.sleep(0)

        async def pulse() -> None:
            await asyncio.sleep(0)
            heartbeat.set()

        await asyncio.create_task(pulse())
        assert heartbeat.is_set()
        with pytest.raises(ContractError) as exc_info:
            await offload.run(lambda: None, message="second")
        assert exc_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert exc_info.value.retryable is True

        release.set()
        worker_name = await first
        assert worker_name.startswith("repository-provenance-persistence")

    asyncio.run(scenario())


def test_provenance_persistence_does_not_use_asyncio_default_executor() -> None:
    offload = RepositoryProvenancePersistenceOffload()

    async def scenario() -> str:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="forbidden-default-persistence",
        ) as default_executor:
            loop.set_default_executor(default_executor)
            return await offload.run(threading.current_thread().name.__class__, message="thread")

    # Use a real callable returning the worker thread name; keep it separate so no work is done
    # before RepositoryProvenancePersistenceOffload submits it to its dedicated executor.
    async def actual_scenario() -> str:
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="forbidden-default-persistence",
        ) as default_executor:
            loop.set_default_executor(default_executor)
            return await offload.run(
                lambda: threading.current_thread().name,
                message="thread",
            )

    worker_name = asyncio.run(actual_scenario())
    assert worker_name.startswith("repository-provenance-persistence")
    assert not worker_name.startswith("forbidden-default-persistence")


def test_provenance_persistence_settles_started_work_before_cancellation() -> None:
    offload = RepositoryProvenancePersistenceOffload()
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def operation() -> None:
        started.set()
        release.wait(timeout=2)
        completed.set()

    async def scenario() -> None:
        task = asyncio.create_task(offload.run(operation, message="cancelled operation"))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    asyncio.run(scenario())


def test_provenance_worker_failure_wins_over_pending_cancellation() -> None:
    offload = RepositoryProvenancePersistenceOffload()
    started = threading.Event()
    release = threading.Event()

    def operation() -> None:
        started.set()
        release.wait(timeout=2)
        raise RuntimeError("worker failure")

    async def scenario() -> None:
        task = asyncio.create_task(offload.run(operation, message="failed operation"))
        while not started.is_set():
            await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(RuntimeError, match="worker failure"):
            await task

    asyncio.run(scenario())


def test_provenance_persistence_maps_raw_sqlite_errors() -> None:
    offload = RepositoryProvenancePersistenceOffload()

    def locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    def corrupt() -> None:
        raise sqlite3.DatabaseError("database disk image is malformed")

    async def scenario() -> None:
        with pytest.raises(ContractError) as busy_info:
            await offload.run(locked, message="provenance persistence failed")
        assert busy_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert busy_info.value.retryable is True

        with pytest.raises(ContractError) as backend_info:
            await offload.run(corrupt, message="provenance persistence failed")
        assert backend_info.value.code is ErrorCode.BACKEND_ERROR
        assert backend_info.value.retryable is False

    asyncio.run(scenario())


def test_provenance_persistence_preserves_existing_contract_error() -> None:
    offload = RepositoryProvenancePersistenceOffload()
    original = ContractError(ErrorCode.CONFLICT, "canonical conflict")

    def fail() -> None:
        raise original

    async def scenario() -> None:
        with pytest.raises(ContractError) as exc_info:
            await offload.run(fail, message="must not replace canonical error")
        assert exc_info.value is original

    asyncio.run(scenario())


def test_sqlite_store_maps_busy_and_other_errors_without_losing_semantics(tmp_path: Path) -> None:
    store = _FailingSqliteStore(tmp_path / "repository-provenance.sqlite3")
    adapter = AsyncRepositoryProvenanceAdapter(store)
    record = _record()

    async def scenario() -> None:
        store.failure = sqlite3.OperationalError("database table is locked")
        with pytest.raises(ContractError) as busy_info:
            await adapter.get(record.run_id, record.repository_id)
        assert busy_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert busy_info.value.retryable is True

        store.failure = sqlite3.DatabaseError("database disk image is malformed")
        with pytest.raises(ContractError) as backend_info:
            await adapter.get(record.run_id, record.repository_id)
        assert backend_info.value.code is ErrorCode.BACKEND_ERROR
        assert backend_info.value.retryable is False

    asyncio.run(scenario())


def test_in_memory_and_sqlite_provenance_share_async_contract(tmp_path: Path) -> None:
    record = _record()
    updated = replace(
        record,
        output_revision="b" * 40,
        diff_artifact_ids=(new_id("artifact"),),
    )
    memory = AsyncRepositoryProvenanceAdapter(RepositoryProvenanceStore())
    sqlite = AsyncRepositoryProvenanceAdapter(
        SqliteRepositoryProvenanceStore(tmp_path / "repository-provenance.sqlite3")
    )

    async def exercise(adapter: AsyncRepositoryProvenanceAdapter):
        await adapter.record(record)
        await adapter.record(record)
        await adapter.upsert(updated)
        return (
            await adapter.get(record.run_id, record.repository_id),
            await adapter.for_run(record.run_id),
        )

    async def scenario() -> None:
        memory_result = await exercise(memory)
        sqlite_result = await exercise(sqlite)
        assert memory_result == sqlite_result == (updated, (updated,))

    asyncio.run(scenario())


def test_async_sqlite_provenance_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "repository-provenance.sqlite3"
    record = _record()

    async def write() -> None:
        await AsyncRepositoryProvenanceAdapter(SqliteRepositoryProvenanceStore(path)).upsert(record)

    async def read() -> RepositoryRunProvenance | None:
        return await AsyncRepositoryProvenanceAdapter(SqliteRepositoryProvenanceStore(path)).get(
            record.run_id,
            record.repository_id,
        )

    asyncio.run(write())
    assert asyncio.run(read()) == record


def test_repository_context_source_offloads_sync_provenance_reader() -> None:
    record = _record()
    store = _SlowProvenanceStore()
    store.record(record)
    adapter = RepositoryContextSourceAdapter(store)
    request = SimpleNamespace(run_id=record.run_id, project_id=None)

    candidates = asyncio.run(adapter.collect(request))  # type: ignore[arg-type]

    assert len(candidates) == 1
    assert candidates[0].source.source_id == record.repository_id
    assert store.thread_names
    assert all(name.startswith("repository-provenance-persistence") for name in store.thread_names)


def test_native_async_provenance_backend_is_not_thread_wrapped() -> None:
    store = _NativeAsyncProvenanceStore()
    runtime = as_async_repository_provenance_store(store)
    record = _record()

    assert runtime is store

    async def scenario() -> None:
        await runtime.upsert(record)
        assert await runtime.get(record.run_id, record.repository_id) == record

    asyncio.run(scenario())
