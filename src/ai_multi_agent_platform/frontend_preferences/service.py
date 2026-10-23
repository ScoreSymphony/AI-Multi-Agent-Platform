"""Awaitable personal frontend preference service."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue

from .models import FrontendPreference
from .repository import FrontendPreferenceRepository

_T = TypeVar("_T")
_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class FrontendPreferencePersistenceOffload:
    """Bound low-frequency UI preference persistence outside asyncio's shared executor."""

    def __init__(self, *, max_pending: int = 32) -> None:
        if max_pending < 1:
            raise ValueError("max_pending must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="frontend-preference-persistence",
        )
        self._lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)

    async def run(self, operation: Callable[[], _T], *, message: str) -> _T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "frontend preference persistence queue capacity exhausted",
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
            folded = str(exc).casefold()
            if isinstance(exc, sqlite3.OperationalError) and any(
                marker in folded for marker in _BUSY_MARKERS
            ):
                raise ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True) from exc
            raise ContractError(ErrorCode.BACKEND_ERROR, message) from exc

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        try:
            with self._lock:
                return operation()
        finally:
            self._capacity.release()


class FrontendPreferenceService:
    def __init__(
        self,
        repository: FrontendPreferenceRepository,
        *,
        offload: FrontendPreferencePersistenceOffload | None = None,
    ) -> None:
        self._repository = repository
        self._offload = offload or FrontendPreferencePersistenceOffload()

    async def get(self, principal_ref: str) -> FrontendPreference:
        stored = await self._offload.run(
            lambda: self._repository.get(principal_ref),
            message="failed to read frontend preference",
        )
        return stored or FrontendPreference.empty(principal_ref)

    async def update(
        self,
        principal_ref: str,
        customization: dict[str, JsonValue],
        *,
        expected_revision: int,
    ) -> FrontendPreference:
        current = await self.get(principal_ref)
        if current.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "frontend preference revision changed",
                details={"current_revision": current.revision},
            )
        candidate = current.next_revision(
            customization,
            now=datetime.now(UTC),
        )
        return await self._offload.run(
            lambda: self._repository.save(candidate, expected_revision=expected_revision),
            message="failed to persist frontend preference",
        )

    async def reset(self, principal_ref: str, *, expected_revision: int) -> FrontendPreference:
        current = await self.get(principal_ref)
        if current.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "frontend preference revision changed",
                details={"current_revision": current.revision},
            )
        await self._offload.run(
            lambda: self._repository.delete(principal_ref, expected_revision=expected_revision),
            message="failed to reset frontend preference",
        )
        return FrontendPreference.empty(principal_ref)


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
