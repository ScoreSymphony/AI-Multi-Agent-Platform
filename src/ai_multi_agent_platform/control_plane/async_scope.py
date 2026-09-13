"""Awaitable Project/Workspace Scope persistence for runtime-critical paths."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, TypeVar

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Project

from .models import OwnerType, WorkspaceIdentity
from .scope_store import ScopeStore

_T = TypeVar("_T")

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class AsyncScopeStore(Protocol):
    """Backend-neutral awaitable Project/Workspace identity persistence."""

    async def create_project(
        self,
        *,
        key: str,
        name: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
    ) -> Project: ...

    async def store_project_snapshot(self, *, key: str, project: Project) -> Project: ...

    async def compensate_project(
        self,
        project_id: str,
        *,
        external_dependencies: tuple[str, ...] | None = None,
    ) -> Project: ...

    async def get_project(self, project_id: str) -> Project: ...

    async def list_projects(self) -> tuple[Project, ...]: ...

    async def create_workspace(
        self,
        *,
        key: str,
        project_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceIdentity: ...

    async def get_workspace(self, workspace_id: str) -> WorkspaceIdentity: ...

    async def list_workspaces(self) -> tuple[WorkspaceIdentity, ...]: ...


class ScopePersistenceOffload:
    """Bound Scope-owned blocking persistence outside asyncio's default executor."""

    def __init__(self, *, max_concurrency: int = 2) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="scope-persistence",
        )
        self._scope_lock = threading.Lock()

    async def run(self, operation: Callable[[], _T]) -> _T:
        loop = asyncio.get_running_loop()
        worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        return await _await_persistence_boundary(worker)

    def _run_sync(self, operation: Callable[[], _T]) -> _T:
        # ScopeStore keeps canonical identity state in memory as well as, for SQLite,
        # durable storage. Serialize reads with writes so another adapter cannot observe
        # an in-memory mutation before its complete persistence operation has settled.
        with self._scope_lock:
            return operation()


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


_SHARED_SCOPE_OFFLOADS: weakref.WeakKeyDictionary[ScopeStore, ScopePersistenceOffload] = (
    weakref.WeakKeyDictionary()
)
_SHARED_SCOPE_OFFLOADS_LOCK = threading.Lock()


def _scope_offload(
    scopes: ScopeStore,
    requested: ScopePersistenceOffload | None,
) -> ScopePersistenceOffload:
    with _SHARED_SCOPE_OFFLOADS_LOCK:
        existing = _SHARED_SCOPE_OFFLOADS.get(scopes)
        if existing is not None:
            return existing
        resolved = requested or ScopePersistenceOffload()
        _SHARED_SCOPE_OFFLOADS[scopes] = resolved
        return resolved


class AsyncScopeStoreAdapter:
    """Awaitable facade over the existing synchronous canonical ScopeStore."""

    def __init__(
        self,
        scopes: ScopeStore,
        *,
        offload: ScopePersistenceOffload | None = None,
    ) -> None:
        self._scopes = scopes
        self._offload = _scope_offload(scopes, offload)

    @property
    def offload(self) -> ScopePersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError:
            raise
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def create_project(
        self,
        *,
        key: str,
        name: str,
        owner_type: OwnerType,
        owner_id: str,
        project_id: str | None = None,
    ) -> Project:
        return await self._run(
            lambda: self._scopes.create_project(
                key=key,
                name=name,
                owner_type=owner_type,
                owner_id=owner_id,
                project_id=project_id,
            ),
            message="failed to persist Project scope",
        )

    async def store_project_snapshot(self, *, key: str, project: Project) -> Project:
        return await self._run(
            lambda: self._scopes.store_project_snapshot(key=key, project=project),
            message="failed to persist Project snapshot",
        )

    async def compensate_project(
        self,
        project_id: str,
        *,
        external_dependencies: tuple[str, ...] | None = None,
    ) -> Project:
        return await self._run(
            lambda: self._scopes.compensate_project(
                project_id,
                external_dependencies=external_dependencies,
            ),
            message="failed to compensate Project scope",
        )

    async def get_project(self, project_id: str) -> Project:
        return await self._run(
            lambda: self._scopes.get_project(project_id),
            message="failed to read Project scope",
        )

    async def list_projects(self) -> tuple[Project, ...]:
        return await self._run(
            self._scopes.list_projects,
            message="failed to list Project scopes",
        )

    async def create_workspace(
        self,
        *,
        key: str,
        project_id: str,
        workspace_id: str | None = None,
    ) -> WorkspaceIdentity:
        return await self._run(
            lambda: self._scopes.create_workspace(
                key=key,
                project_id=project_id,
                workspace_id=workspace_id,
            ),
            message="failed to persist Workspace identity",
        )

    async def get_workspace(self, workspace_id: str) -> WorkspaceIdentity:
        return await self._run(
            lambda: self._scopes.get_workspace(workspace_id),
            message="failed to read Workspace identity",
        )

    async def list_workspaces(self) -> tuple[WorkspaceIdentity, ...]:
        return await self._run(
            self._scopes.list_workspaces,
            message="failed to list Workspace identities",
        )


__all__ = ["AsyncScopeStore", "AsyncScopeStoreAdapter", "ScopePersistenceOffload"]
