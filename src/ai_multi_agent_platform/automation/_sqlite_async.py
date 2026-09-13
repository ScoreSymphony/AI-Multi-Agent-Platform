"""Async offload primitives for the Automation SQLite backends."""

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
        Connections are created, used and closed entirely inside ``operation`` on the worker
        thread; callers must never pass live sqlite3 connection objects across this boundary.
        """

        if write:
            # Waiting writers must not consume shared offload capacity. Reads can continue
            # through separate SQLite connections while one serialized write is active.
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
        # Repeated Task.cancel() calls must not release the repository guard while the
        # underlying to_thread operation is still committing or rolling back.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        try:
            worker.result()
        except Exception:
            # The caller is already cancelled. We only wait to settle the transaction before
            # propagating cancellation; the worker exception does not replace cancellation.
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
