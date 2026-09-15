"""Async offload primitives for Organization-owned SQLite persistence."""

from __future__ import annotations

import asyncio
import sqlite3
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


class AsyncSqliteOffload:
    """Bound and serialize blocking Organization SQLite work off the event loop."""

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = asyncio.Semaphore(max_concurrency)
        self._write_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="organization-sqlite-persistence",
        )

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        if write:
            async with self._write_lock:
                async with self._slots:
                    return await _run_to_transaction_boundary(self._executor, operation)
        async with self._slots:
            return await _run_to_transaction_boundary(self._executor, operation)


async def _run_to_transaction_boundary[T](
    executor: ThreadPoolExecutor,
    operation: Callable[[], T],
) -> T:
    loop = asyncio.get_running_loop()
    worker = loop.run_in_executor(executor, operation)
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
            pass
        raise


def map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    """Map SQLite busy/locked causes while preserving domain-level repository errors."""

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
