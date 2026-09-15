from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from types import SimpleNamespace
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.governance.async_persistence import GovernancePersistenceOffload
from ai_multi_agent_platform.governance.async_runtime import AsyncGovernanceRuntime
from ai_multi_agent_platform.governance.service import GovernanceService


class _SlowRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.thread_names: list[str] = []

    def list_proposals(self) -> tuple[()]:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.thread_names.append(threading.current_thread().name)
        try:
            time.sleep(0.03)
            return ()
        finally:
            with self._lock:
                self.active -= 1


def _runtime(repository: object) -> AsyncGovernanceRuntime:
    service = cast(GovernanceService, SimpleNamespace(repository=repository))
    return AsyncGovernanceRuntime(service)


def test_governance_runtime_offloads_and_serializes_shared_repository() -> None:
    repository = _SlowRepository()
    first = _runtime(repository)
    second = _runtime(repository)

    async def scenario() -> None:
        await asyncio.gather(first.list_proposals(), second.list_proposals())

    asyncio.run(scenario())

    assert repository.max_active == 1
    assert repository.thread_names
    assert all(name.startswith("governance-persistence") for name in repository.thread_names)


def test_governance_persistence_queue_is_bounded_without_blocking_loop() -> None:
    offload = GovernancePersistenceOffload(max_concurrency=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_operation() -> str:
        started.set()
        release.wait(timeout=2)
        return threading.current_thread().name

    async def scenario() -> None:
        first = asyncio.create_task(offload.run(blocking_operation, message="first"))
        while not started.is_set():
            await asyncio.sleep(0)

        # Reaching this await while the worker is blocked proves the event loop remains responsive.
        await asyncio.sleep(0)
        with pytest.raises(ContractError) as exc_info:
            await offload.run(lambda: None, message="second")
        assert exc_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert exc_info.value.retryable is True

        release.set()
        worker_name = await first
        assert worker_name.startswith("governance-persistence")

    asyncio.run(scenario())


def test_governance_persistence_maps_busy_sqlite_to_retryable_failure() -> None:
    offload = GovernancePersistenceOffload()

    def locked() -> None:
        raise sqlite3.OperationalError("database is locked")

    async def scenario() -> None:
        with pytest.raises(ContractError) as exc_info:
            await offload.run(locked, message="governance persistence failed")
        assert exc_info.value.code is ErrorCode.TRANSIENT_FAILURE
        assert exc_info.value.retryable is True

    asyncio.run(scenario())


def test_governance_persistence_settles_started_work_before_cancellation() -> None:
    offload = GovernancePersistenceOffload()
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
