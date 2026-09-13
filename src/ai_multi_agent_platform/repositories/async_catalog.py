"""Async-facing repository binding catalog adapters.

The durable SQLite catalog remains a dependency-free synchronous implementation for
constructor/startup tooling. Runtime services use this module so blocking SQLite work is
bounded and never runs on the asyncio event-loop thread.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .catalog import RepositoryBindingRecord, SqliteRepositoryBindingCatalog

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class RepositoryBindingCatalog(Protocol):
    """Backend-neutral async persistence seam used by repository runtime services."""

    async def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord: ...

    async def get(self, repository_id: str) -> RepositoryBindingRecord: ...

    async def list(
        self,
        *,
        connection_id: str | None = None,
    ) -> tuple[RepositoryBindingRecord, ...]: ...

    async def delete(self, repository_id: str) -> None: ...


class InMemoryRepositoryBindingCatalog:
    """Deterministic async catalog for contract/unit tests and non-durable compositions."""

    def __init__(self) -> None:
        self._records: dict[str, RepositoryBindingRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
        async with self._lock:
            self._records[record.repository_id] = record
        return record

    async def get(self, repository_id: str) -> RepositoryBindingRecord:
        async with self._lock:
            record = self._records.get(repository_id)
        if record is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"repository binding not found: {repository_id}",
            )
        return record

    async def list(
        self,
        *,
        connection_id: str | None = None,
    ) -> tuple[RepositoryBindingRecord, ...]:
        async with self._lock:
            records = tuple(self._records.values())
        if connection_id is not None:
            records = tuple(record for record in records if record.connection_id == connection_id)
        return tuple(sorted(records, key=lambda record: record.repository_id))

    async def delete(self, repository_id: str) -> None:
        async with self._lock:
            if repository_id not in self._records:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"repository binding not found: {repository_id}",
                )
            del self._records[repository_id]


class AsyncSqliteRepositoryBindingCatalog:
    """Event-loop-safe adapter over the synchronous SQLite binding catalog.

    Each blocking call opens, uses and closes its SQLite connection inside the worker thread
    because the wrapped catalog owns connection creation per operation. Writes are serialized
    before they consume shared worker capacity; reads may run concurrently up to
    ``max_concurrency``. Cancellation is deferred until the synchronous operation reaches its
    commit/rollback boundary.
    """

    def __init__(
        self,
        catalog: SqliteRepositoryBindingCatalog,
        *,
        max_concurrency: int = 4,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._catalog = catalog
        self._slots = asyncio.Semaphore(max_concurrency)
        self._write_lock = asyncio.Lock()

    async def save(self, record: RepositoryBindingRecord) -> RepositoryBindingRecord:
        return await self._run(
            lambda: self._catalog.save(record),
            write=True,
            message="failed to persist repository binding",
        )

    async def get(self, repository_id: str) -> RepositoryBindingRecord:
        return await self._run(
            lambda: self._catalog.get(repository_id),
            write=False,
            message="failed to read repository binding",
        )

    async def list(
        self,
        *,
        connection_id: str | None = None,
    ) -> tuple[RepositoryBindingRecord, ...]:
        return await self._run(
            lambda: self._catalog.list(connection_id=connection_id),
            write=False,
            message="failed to list repository bindings",
        )

    async def delete(self, repository_id: str) -> None:
        await self._run(
            lambda: self._catalog.delete(repository_id),
            write=True,
            message="failed to delete repository binding",
        )

    async def _run[T](
        self,
        operation: Callable[[], T],
        *,
        write: bool,
        message: str,
    ) -> T:
        try:
            if write:
                async with self._write_lock:
                    async with self._slots:
                        return await _run_to_transaction_boundary(operation)
            async with self._slots:
                return await _run_to_transaction_boundary(operation)
        except ContractError as exc:
            mapped = _map_contract_sqlite_error(exc, message)
            if mapped is exc:
                raise
            raise mapped from exc.__cause__
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc


CatalogLike = RepositoryBindingCatalog | SqliteRepositoryBindingCatalog


def ensure_async_repository_binding_catalog(
    catalog: CatalogLike,
    *,
    max_concurrency: int = 4,
) -> RepositoryBindingCatalog:
    """Adapt the legacy synchronous SQLite catalog at a runtime composition boundary."""

    if isinstance(catalog, SqliteRepositoryBindingCatalog):
        return AsyncSqliteRepositoryBindingCatalog(catalog, max_concurrency=max_concurrency)
    return catalog


async def _run_to_transaction_boundary[T](operation: Callable[[], T]) -> T:
    worker = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Repeated cancellation must not release the catalog guard while the worker may still
        # be committing or rolling back. Cancellation remains authoritative only when the
        # settled worker has not produced a failure of its own.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def _map_contract_sqlite_error(exc: ContractError, message: str) -> ContractError:
    cause = exc.__cause__
    if (
        exc.code is ErrorCode.BACKEND_ERROR
        and isinstance(cause, sqlite3.OperationalError)
        and _is_busy_error(cause)
    ):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return exc


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and _is_busy_error(exc):
        return ContractError(
            ErrorCode.TRANSIENT_FAILURE,
            message,
            retryable=True,
        )
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
    normalized = str(exc).casefold()
    return any(marker in normalized for marker in _BUSY_MARKERS)
