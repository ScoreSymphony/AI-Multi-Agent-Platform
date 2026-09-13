"""Async offload primitives for Connector-owned SQLite persistence."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncSqliteOffload:
    """Run bounded Connector SQLite work away from the asyncio event-loop thread."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="connector-sqlite",
        )
        self._write_lock = threading.Lock()

    async def run[T](self, operation: Callable[[], T], *, write: bool = False) -> T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(self._executor, self._run_sync, operation, write)
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Repeated caller cancellation must not release the logical repository boundary
            # before the synchronous SQLite transaction has committed or rolled back.
            while not worker.done():
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    continue
            failure = worker.exception()
            if failure is not None:
                raise failure from None
            raise

    def _run_sync[T](self, operation: Callable[[], T], write: bool) -> T:
        if write:
            with self._write_lock:
                return operation()
        return operation()


def map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    """Translate SQLite driver failures to canonical persistence semantics."""

    if isinstance(exc, sqlite3.OperationalError):
        normalized = str(exc).casefold()
        if any(marker in normalized for marker in _BUSY_MARKERS):
            return ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                message,
                retryable=True,
            )
    return ContractError(ErrorCode.BACKEND_ERROR, message)
