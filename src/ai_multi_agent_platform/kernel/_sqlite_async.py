"""Async offload primitives for the stdlib SQLite kernel backend."""

from __future__ import annotations

import asyncio
import sqlite3
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
    """Bound and serialize blocking SQLite work without sharing connections across threads."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = asyncio.Semaphore(max_concurrency)
        self._write_lock = asyncio.Lock()

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        """Run one complete synchronous SQLite operation outside the event-loop thread.

        Cancellation is deferred until the worker operation reaches its transaction boundary.
        This prevents a cancelled coroutine from abandoning a still-running commit/rollback
        while the caller assumes the database operation has already stopped.
        """

        if write:
            # Waiting writers must not consume the shared offload capacity. SQLite WAL can
            # serve reads through separate connections while one serialized write is active.
            async with self._write_lock:
                async with self._slots:
                    return await _run_to_transaction_boundary(operation)
        async with self._slots:
            return await _run_to_transaction_boundary(operation)


async def _run_to_transaction_boundary[T](operation: Callable[[], T]) -> T:
    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A Task may be cancelled repeatedly (for example timeout followed by disconnect).
        # Keep shielding the worker until the synchronous transaction has actually settled;
        # releasing repository locks/semaphore capacity earlier would let another operation
        # overlap a still-running SQLite transaction in the worker thread.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        try:
            worker.result()
        except Exception:
            # The caller is already cancelled. Waiting here is solely to settle the
            # database transaction before cancellation crosses the repository boundary.
            pass
        raise


def map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    """Translate driver-specific failures into canonical persistence semantics."""

    if isinstance(exc, sqlite3.OperationalError):
        normalized = str(exc).casefold()
        if any(marker in normalized for marker in _BUSY_MARKERS):
            return ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                message,
                retryable=True,
            )
    return ContractError(ErrorCode.BACKEND_ERROR, message)
