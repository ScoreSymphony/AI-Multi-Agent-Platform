"""Dedicated persistence offload for workspace-retention metadata."""

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


class WorkspaceRetentionPersistenceOffload:
    """Run retention SQLite work on a dedicated bounded executor.

    Retention mutations replace the complete durable state snapshot.  One worker therefore
    preserves their existing serialization semantics, while ``max_pending`` bounds work before
    executor submission so a blocked SQLite transaction cannot consume asyncio's default executor
    or grow an unbounded queue.
    """

    def __init__(
        self,
        *,
        max_pending: int = 64,
        thread_name_prefix: str = "workspace-retention-persistence",
    ) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=thread_name_prefix,
        )
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._thread_name_prefix = thread_name_prefix
        self.max_concurrency = 1
        self.max_pending = max_pending

    async def run(self, operation: Callable[[], _T], *, message: str) -> _T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                f"{self._thread_name_prefix} queue capacity exhausted",
                retryable=True,
            )

        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise

        try:
            return await _await_transaction_boundary(worker)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        finally:
            self._capacity.release()


async def _await_transaction_boundary[T](worker: asyncio.Future[T]) -> T:
    """Let started persistence settle; a worker failure wins pending cancellation."""

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


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


__all__ = ["WorkspaceRetentionPersistenceOffload"]
