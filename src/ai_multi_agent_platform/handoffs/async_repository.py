"""Awaitable Handoff repository boundary for blocking persistence backends."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar
from weakref import WeakKeyDictionary

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .models import AgentHandoff, HandoffConsumption
from .repository import HandoffRepository

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncHandoffRepository(Protocol):
    """Backend-neutral awaitable Handoff persistence contract for runtime callers."""

    async def create_handoff(
        self,
        handoff: AgentHandoff,
        *,
        idempotency_key: str,
        request_digest: str,
        expected_previous_revision: int,
    ) -> tuple[AgentHandoff, bool]: ...

    async def get_handoff(
        self,
        handoff_id: str,
        revision: int | None = None,
    ) -> AgentHandoff: ...

    async def list_handoffs(self) -> tuple[AgentHandoff, ...]: ...

    async def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]: ...

    async def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]: ...

    async def bind_consumption(
        self,
        consumption: HandoffConsumption,
    ) -> tuple[HandoffConsumption, bool]: ...

    async def list_consumptions(
        self,
        handoff_id: str,
        revision: int,
    ) -> tuple[HandoffConsumption, ...]: ...

    async def list_consumptions_for_run(
        self,
        run_id: str,
    ) -> tuple[HandoffConsumption, ...]: ...


class HandoffPersistenceOffload:
    """Serialize and bound synchronous Handoff persistence outside the event loop."""

    def __init__(self, *, max_concurrency: int = 2, max_pending: int = 64) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="handoff-persistence",
        )
        self._repository_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run(self, operation: Callable[[], _T]) -> _T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Handoff persistence queue capacity exhausted",
                retryable=True,
            )
        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        try:
            with self._repository_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_HANDOFF_OFFLOADS: WeakKeyDictionary[object, HandoffPersistenceOffload] = WeakKeyDictionary()
_SHARED_HANDOFF_OFFLOADS_LOCK = threading.Lock()


def _handoff_offload(
    repository: HandoffRepository,
    requested: HandoffPersistenceOffload | None,
) -> HandoffPersistenceOffload:
    with _SHARED_HANDOFF_OFFLOADS_LOCK:
        existing = _SHARED_HANDOFF_OFFLOADS.get(repository)
        if existing is not None:
            return existing
        resolved = requested or HandoffPersistenceOffload()
        _SHARED_HANDOFF_OFFLOADS[repository] = resolved
        return resolved


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
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


class AsyncHandoffRepositoryAdapter:
    """Awaitable facade over the canonical synchronous Handoff repository."""

    def __init__(
        self,
        repository: HandoffRepository,
        *,
        offload: HandoffPersistenceOffload | None = None,
    ) -> None:
        self._repository = repository
        self._offload = _handoff_offload(repository, offload)

    @property
    def offload(self) -> HandoffPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError:
            # The synchronous repository already maps semantic persistence conflicts to
            # backend-neutral ContractError values. Preserve those mappings so SQLite and
            # InMemory implementations retain identical revision/idempotency semantics.
            raise
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def create_handoff(
        self,
        handoff: AgentHandoff,
        *,
        idempotency_key: str,
        request_digest: str,
        expected_previous_revision: int,
    ) -> tuple[AgentHandoff, bool]:
        return await self._run(
            lambda: self._repository.create_handoff(
                handoff,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                expected_previous_revision=expected_previous_revision,
            ),
            message="failed to persist Handoff",
        )

    async def get_handoff(
        self,
        handoff_id: str,
        revision: int | None = None,
    ) -> AgentHandoff:
        return await self._run(
            lambda: self._repository.get_handoff(handoff_id, revision),
            message="failed to read Handoff",
        )

    async def list_handoffs(self) -> tuple[AgentHandoff, ...]:
        return await self._run(
            self._repository.list_handoffs,
            message="failed to list Handoffs",
        )

    async def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]:
        return await self._run(
            lambda: self._repository.list_handoffs_for_task(task_id),
            message="failed to list Task Handoffs",
        )

    async def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]:
        return await self._run(
            lambda: self._repository.list_handoffs_for_step(step_id),
            message="failed to list Step Handoffs",
        )

    async def bind_consumption(
        self,
        consumption: HandoffConsumption,
    ) -> tuple[HandoffConsumption, bool]:
        return await self._run(
            lambda: self._repository.bind_consumption(consumption),
            message="failed to persist Handoff consumption",
        )

    async def list_consumptions(
        self,
        handoff_id: str,
        revision: int,
    ) -> tuple[HandoffConsumption, ...]:
        return await self._run(
            lambda: self._repository.list_consumptions(handoff_id, revision),
            message="failed to list Handoff consumptions",
        )

    async def list_consumptions_for_run(
        self,
        run_id: str,
    ) -> tuple[HandoffConsumption, ...]:
        return await self._run(
            lambda: self._repository.list_consumptions_for_run(run_id),
            message="failed to list consuming Run Handoffs",
        )


def runtime_handoff_repository(
    repository: HandoffRepository,
    *,
    runtime_repository: AsyncHandoffRepository | None = None,
) -> AsyncHandoffRepository:
    """Resolve the awaitable runtime contract while preserving the sync compatibility seam."""

    if runtime_repository is not None:
        return runtime_repository
    return AsyncHandoffRepositoryAdapter(repository)


__all__ = [
    "AsyncHandoffRepository",
    "AsyncHandoffRepositoryAdapter",
    "HandoffPersistenceOffload",
    "runtime_handoff_repository",
]
