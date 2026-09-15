"""Bounded async offload support for Data-owned blocking local persistence."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

_T = TypeVar("_T")

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncDataOffload:
    """Run complete blocking Data persistence operations away from the event loop.

    Each provider owns dedicated bounded read/write executors instead of consuming asyncio's
    process-wide default executor. Writes use a single-worker executor, so queued durable
    mutations never occupy read workers while waiting for write serialization. A shared slot
    bound still caps concurrently executing provider operations across both executors and keeps
    one local provider valid when callers reuse it across multiple event-loop lifetimes.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._read_executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="data-persistence-read",
        )
        self._write_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="data-persistence-write",
        )

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        loop = asyncio.get_running_loop()
        executor = self._write_executor if write else self._read_executor
        worker = loop.run_in_executor(executor, self._run_sync, operation)
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        with self._slots:
            return operation()


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    """Translate raw SQLite failures into canonical Data persistence errors."""

    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    """Promote SQLite contention wrapped by an adapter to a retryable transient failure."""

    cause = exc.__cause__
    if (
        exc.code is ErrorCode.BACKEND_ERROR
        and isinstance(cause, sqlite3.OperationalError)
        and any(marker in str(cause).casefold() for marker in _BUSY_MARKERS)
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return exc
