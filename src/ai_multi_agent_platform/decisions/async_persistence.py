"""Dedicated awaitable persistence boundary for Decision Record runtime paths."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class DecisionPersistenceOffload:
    """Serialize and bound one synchronous Decision persistence authority."""

    def __init__(
        self,
        *,
        thread_name_prefix: str = "decision-persistence",
        max_concurrency: int = 1,
        max_pending: int = 64,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix=thread_name_prefix,
        )
        self._store_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self._thread_name_prefix = thread_name_prefix
        self.max_concurrency = max_concurrency
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
            return await _await_persistence_boundary(worker)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        try:
            with self._store_lock:
                return operation()
        finally:
            self._capacity.release()


_DECISION_REPOSITORY_OFFLOADS = SharedPersistenceOffloadRegistry[DecisionPersistenceOffload]()


def decision_persistence_offload(
    repository: object,
    *,
    owner: object,
    requested: DecisionPersistenceOffload | None = None,
) -> DecisionPersistenceOffload:
    """Resolve the shared serialization boundary for one Decision repository identity."""

    return _DECISION_REPOSITORY_OFFLOADS.resolve(
        repository,
        owner=owner,
        requested=requested,
        factory=DecisionPersistenceOffload,
    )


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    """Let started persistence settle; worker failure wins pending cancellation."""

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


__all__ = ["DecisionPersistenceOffload", "decision_persistence_offload"]
