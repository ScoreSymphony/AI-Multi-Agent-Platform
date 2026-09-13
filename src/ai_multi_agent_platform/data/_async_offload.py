"""Bounded async offload support for Data-owned blocking local persistence."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
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

    The worker-side gates are deliberately thread-based rather than asyncio-bound so one local
    provider remains valid when callers reuse it across multiple event-loop lifetimes. The
    process' default executor bounds actual threads, while ``_slots`` explicitly bounds active
    provider operations. Writers queue before consuming a shared slot, preserving read capacity
    while one durable mutation is active.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._write_lock = threading.Lock()

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        worker = asyncio.create_task(asyncio.to_thread(self._run_sync, operation, write))
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T], write: bool) -> _T:
        if write:
            with self._write_lock:
                with self._slots:
                    return operation()
        with self._slots:
            return operation()


async def _await_persistence_boundary[T](worker: asyncio.Task[T]) -> T:
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
