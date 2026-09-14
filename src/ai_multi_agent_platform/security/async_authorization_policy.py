"""Awaitable local authorization-policy persistence for async runtime callers."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .authorization import LocalPrincipalPolicy

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class LocalAuthorizationPolicyStore(Protocol):
    """Synchronous policy store contract adapted at the runtime boundary."""

    def has_policy(self, principal_ref: str) -> bool: ...

    def register(self, policy: LocalPrincipalPolicy) -> None: ...


class AsyncAuthorizationPolicyService(Protocol):
    """Backend-neutral awaitable policy-registration semantics."""

    async def has_policy(self, principal_ref: str) -> bool: ...

    async def ensure_registered(self, policy: LocalPrincipalPolicy) -> bool: ...


class AuthorizationPolicyPersistenceOffload:
    """Bound and serialize blocking policy persistence outside the event loop."""

    def __init__(self, *, max_concurrency: int = 1, max_pending: int = 32) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="authorization-policy-persistence",
        )
        self._serialization_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run[T](self, operation: Callable[[], T]) -> T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Authorization policy persistence queue capacity exhausted",
                retryable=True,
            )
        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise
        return await _await_persistence_boundary(worker)

    def _run_sync[T](self, operation: Callable[[], T]) -> T:
        try:
            with self._serialization_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_POLICY_OFFLOADS = SharedPersistenceOffloadRegistry[AuthorizationPolicyPersistenceOffload]()


class AsyncAuthorizationPolicyServiceAdapter:
    """Awaitable facade over a synchronous local authorization policy store."""

    def __init__(
        self,
        store: LocalAuthorizationPolicyStore,
        *,
        offload: AuthorizationPolicyPersistenceOffload | None = None,
    ) -> None:
        self._store = store
        self._offload = _SHARED_POLICY_OFFLOADS.resolve(
            store,
            owner=self,
            requested=offload,
            factory=AuthorizationPolicyPersistenceOffload,
        )

    @property
    def offload(self) -> AuthorizationPolicyPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def has_policy(self, principal_ref: str) -> bool:
        return await self._run(
            lambda: self._store.has_policy(principal_ref),
            message="failed to read authorization policy",
        )

    async def ensure_registered(self, policy: LocalPrincipalPolicy) -> bool:
        def ensure() -> bool:
            if self._store.has_policy(policy.principal_ref):
                return False
            self._store.register(policy)
            return True

        return await self._run(
            ensure,
            message="failed to persist authorization policy",
        )


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


def _sqlite_error(exc: BaseException) -> sqlite3.Error | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, sqlite3.Error):
            return current
        current = current.__cause__
    return None


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


def runtime_authorization_policy_service(
    store: LocalAuthorizationPolicyStore,
    *,
    runtime_service: AsyncAuthorizationPolicyService | None = None,
) -> AsyncAuthorizationPolicyService:
    if runtime_service is not None:
        return runtime_service
    return AsyncAuthorizationPolicyServiceAdapter(store)


__all__ = [
    "AsyncAuthorizationPolicyService",
    "AsyncAuthorizationPolicyServiceAdapter",
    "AuthorizationPolicyPersistenceOffload",
    "LocalAuthorizationPolicyStore",
    "runtime_authorization_policy_service",
]
