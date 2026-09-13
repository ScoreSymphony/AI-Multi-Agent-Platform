"""Async offload primitives for Notification-owned SQLite backends."""

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


class AsyncSqliteOffload:
    """Bound and serialize blocking Notification SQLite work off the event loop.

    Thread-based gates keep one repository instance reusable across separate asyncio event-loop
    lifetimes while preserving a bounded number of active provider operations. Writers acquire
    the mutation gate before shared capacity so queued writes do not consume read capacity.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._write_lock = threading.Lock()

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        """Run a complete synchronous SQLite operation outside the event-loop thread.

        Cancellation is deferred until the worker operation reaches its commit/rollback boundary.
        Callers create, use and close SQLite connections entirely inside ``operation``.
        """

        worker = asyncio.create_task(asyncio.to_thread(self._run_sync, operation, write))
        return await _run_to_transaction_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T], write: bool) -> _T:
        if write:
            with self._write_lock:
                with self._slots:
                    return operation()
        with self._slots:
            return operation()


async def _run_to_transaction_boundary[T](worker: asyncio.Task[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        try:
            worker.result()
        except Exception:
            # Cancellation remains authoritative after the transaction has settled.
            pass
        raise


def map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    """Translate driver-specific SQLite failures into canonical persistence semantics."""

    if isinstance(exc, sqlite3.OperationalError):
        normalized = str(exc).casefold()
        if any(marker in normalized for marker in _BUSY_MARKERS):
            return ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                message,
                retryable=True,
            )
    return ContractError(ErrorCode.BACKEND_ERROR, message)
