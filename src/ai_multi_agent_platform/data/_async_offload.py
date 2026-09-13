"""Bounded async offload support for Data-owned blocking local persistence."""

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


class AsyncDataOffload:
    """Run complete blocking Data persistence operations away from the event loop.

    Writes queue before consuming shared capacity so a backlog of reads cannot indefinitely
    starve durable mutations. Cancellation is deferred until the worker reaches its persistence
    boundary; a worker failure remains authoritative so adapter cleanup/rollback is observable.
    """

    def __init__(self, *, max_concurrency: int = 4) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._slots = asyncio.Semaphore(max_concurrency)
        self._write_lock = asyncio.Lock()

    async def run(self, operation: Callable[[], _T], *, write: bool = False) -> _T:
        if write:
            async with self._write_lock:
                async with self._slots:
                    return await _run_to_persistence_boundary(operation)
        async with self._slots:
            return await _run_to_persistence_boundary(operation)


async def _run_to_persistence_boundary[T](operation: Callable[[], T]) -> T:
    worker = asyncio.create_task(asyncio.to_thread(operation))
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


def map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    """Promote SQLite contention to the canonical retryable transient failure contract."""

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
