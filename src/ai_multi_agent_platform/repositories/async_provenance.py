"""Awaitable, bounded persistence boundary for repository Run provenance."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .models import RepositoryRunProvenance

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class RepositoryProvenanceGetReader(Protocol):
    def get(self, run_id: str, repository_id: str) -> RepositoryRunProvenance | None: ...


class RepositoryProvenanceReader(RepositoryProvenanceGetReader, Protocol):
    def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]: ...


class RepositoryProvenanceWriter(Protocol):
    def record(self, provenance: RepositoryRunProvenance) -> None: ...

    def upsert(self, provenance: RepositoryRunProvenance) -> None: ...


class RepositoryProvenanceStore(
    RepositoryProvenanceReader,
    RepositoryProvenanceWriter,
    Protocol,
):
    pass


class AsyncRepositoryProvenanceGetReader(Protocol):
    async def get(
        self,
        run_id: str,
        repository_id: str,
    ) -> RepositoryRunProvenance | None: ...


class AsyncRepositoryProvenanceReader(AsyncRepositoryProvenanceGetReader, Protocol):
    async def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]: ...


class AsyncRepositoryProvenanceStore(AsyncRepositoryProvenanceReader, Protocol):
    async def record(self, provenance: RepositoryRunProvenance) -> None: ...

    async def upsert(self, provenance: RepositoryRunProvenance) -> None: ...


class RepositoryProvenancePersistenceOffload:
    """Serialize and bound one synchronous repository-provenance backing store."""

    def __init__(
        self,
        *,
        thread_name_prefix: str = "repository-provenance-persistence",
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


_PROVENANCE_OFFLOADS = SharedPersistenceOffloadRegistry[RepositoryProvenancePersistenceOffload]()


class AsyncRepositoryProvenanceAdapter:
    """Backend-neutral awaitable view over one synchronous provenance authority."""

    def __init__(
        self,
        store: RepositoryProvenanceStore,
        *,
        persistence_offload: RepositoryProvenancePersistenceOffload | None = None,
    ) -> None:
        self._store = store
        self._offload = _PROVENANCE_OFFLOADS.resolve(
            store,
            owner=self,
            requested=persistence_offload,
            factory=RepositoryProvenancePersistenceOffload,
        )

    async def record(self, provenance: RepositoryRunProvenance) -> None:
        await self._offload.run(
            lambda: self._store.record(provenance),
            message="failed to persist repository Run provenance",
        )

    async def upsert(self, provenance: RepositoryRunProvenance) -> None:
        await self._offload.run(
            lambda: self._store.upsert(provenance),
            message="failed to update repository Run provenance",
        )

    async def get(
        self,
        run_id: str,
        repository_id: str,
    ) -> RepositoryRunProvenance | None:
        return await self._offload.run(
            lambda: self._store.get(run_id, repository_id),
            message="failed to read repository Run provenance",
        )

    async def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        return await self._offload.run(
            lambda: self._store.for_run(run_id),
            message="failed to list repository Run provenance",
        )


class AsyncRepositoryProvenanceGetAdapter:
    """Awaitable read-only bridge for narrow provenance readers used by runtime adapters."""

    def __init__(
        self,
        reader: RepositoryProvenanceGetReader,
        *,
        persistence_offload: RepositoryProvenancePersistenceOffload | None = None,
    ) -> None:
        self._reader = reader
        self._offload = _PROVENANCE_OFFLOADS.resolve(
            reader,
            owner=self,
            requested=persistence_offload,
            factory=RepositoryProvenancePersistenceOffload,
        )

    async def get(
        self,
        run_id: str,
        repository_id: str,
    ) -> RepositoryRunProvenance | None:
        return await self._offload.run(
            lambda: self._reader.get(run_id, repository_id),
            message="failed to read repository Run provenance",
        )


class AsyncRepositoryProvenanceReaderAdapter(AsyncRepositoryProvenanceGetAdapter):
    def __init__(
        self,
        reader: RepositoryProvenanceReader,
        *,
        persistence_offload: RepositoryProvenancePersistenceOffload | None = None,
    ) -> None:
        super().__init__(reader, persistence_offload=persistence_offload)
        self._full_reader = reader

    async def for_run(self, run_id: str) -> tuple[RepositoryRunProvenance, ...]:
        return await self._offload.run(
            lambda: self._full_reader.for_run(run_id),
            message="failed to list repository Run provenance",
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


__all__ = [
    "AsyncRepositoryProvenanceAdapter",
    "AsyncRepositoryProvenanceGetAdapter",
    "AsyncRepositoryProvenanceGetReader",
    "AsyncRepositoryProvenanceReader",
    "AsyncRepositoryProvenanceReaderAdapter",
    "AsyncRepositoryProvenanceStore",
    "RepositoryProvenanceGetReader",
    "RepositoryProvenancePersistenceOffload",
    "RepositoryProvenanceReader",
    "RepositoryProvenanceStore",
]
