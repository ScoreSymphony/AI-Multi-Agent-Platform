"""Async Project/Workspace Scope persistence regressions for issue #892."""

from __future__ import annotations

import asyncio
import gc
import sqlite3
import threading
import time
import weakref
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.async_scope import (
    AsyncScopeStoreAdapter,
    ScopePersistenceOffload,
)
from ai_multi_agent_platform.control_plane.sqlite_scope import SqliteScopeStore


class _RecordingScopeStore(SqliteScopeStore):
    def __init__(self, path: Path, *, delay: float = 0.05) -> None:
        self.connection_threads: list[int] = []
        self.delay_connections = False
        self._delay = delay
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        if self.delay_connections:
            time.sleep(self._delay)
        return super()._connect()


class _BlockingScopeStore(SqliteScopeStore):
    def __init__(self, path: Path) -> None:
        self.block_connections = False
        self.started = threading.Event()
        self.release = threading.Event()
        super().__init__(path)

    def arm(self) -> None:
        self.started.clear()
        self.release.clear()
        self.block_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.block_connections:
            self.started.set()
            if not self.release.wait(timeout=3):
                raise TimeoutError("test Scope persistence release timed out")
        return super()._connect()


class _BusyScopeStore(SqliteScopeStore):
    def __init__(self, path: Path) -> None:
        self.busy = False
        super().__init__(path)
        self.busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect()


def test_scope_sqlite_runtime_is_responsive_and_worker_owned(tmp_path: Path) -> None:
    async def scenario() -> None:
        scopes = _RecordingScopeStore(tmp_path / "scope.sqlite3")
        adapter = AsyncScopeStoreAdapter(scopes)
        event_loop_thread = threading.get_ident()
        before = len(scopes.connection_threads)

        write = asyncio.create_task(
            adapter.create_project(
                key="project-create",
                name="Async Project",
                owner_type="user",
                owner_id="user:alice",
            )
        )
        await asyncio.sleep(0.01)

        assert not write.done()
        project = await write
        assert project.name == "Async Project"
        runtime_threads = scopes.connection_threads[before:]
        assert runtime_threads
        assert all(thread_id != event_loop_thread for thread_id in runtime_threads)

    asyncio.run(scenario())


def test_scope_persistence_uses_dedicated_bounded_executor() -> None:
    async def scenario() -> None:
        offload = ScopePersistenceOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def blocked() -> int:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("test Scope worker release timed out")
            return threading.get_ident()

        pending = [asyncio.create_task(offload.run(blocked)) for _ in range(8)]
        assert await asyncio.to_thread(started.wait, 1)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(threading.get_ident),
                timeout=0.5,
            )
            assert default_worker != threading.get_ident()
            assert any(not task.done() for task in pending)
        finally:
            release.set()

        worker_threads = await asyncio.gather(*pending)
        assert len(set(worker_threads)) == 1

    asyncio.run(scenario())


def test_independent_scope_adapters_share_serialization(tmp_path: Path) -> None:
    scopes = SqliteScopeStore(tmp_path / "scope.sqlite3")
    first = AsyncScopeStoreAdapter(scopes)
    second = AsyncScopeStoreAdapter(scopes)

    assert first.offload is second.offload


def test_shared_scope_runtime_is_weakly_owned(tmp_path: Path) -> None:
    scopes = SqliteScopeStore(tmp_path / "scope.sqlite3")
    adapter = AsyncScopeStoreAdapter(scopes)
    scope_ref = weakref.ref(scopes)
    offload_ref = weakref.ref(adapter.offload)

    del adapter
    del scopes
    gc.collect()

    assert scope_ref() is None
    assert offload_ref() is None


def test_scope_read_waits_for_inflight_write_and_restart_sees_commit(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "scope.sqlite3"
        scopes = _BlockingScopeStore(database)
        writer = AsyncScopeStoreAdapter(scopes)
        reader = AsyncScopeStoreAdapter(scopes)
        scopes.arm()

        write = asyncio.create_task(
            writer.create_project(
                key="project-create",
                name="Serialized Project",
                owner_type="user",
                owner_id="user:alice",
                project_id="project_serialized",
            )
        )
        assert await asyncio.to_thread(scopes.started.wait, 1)

        read = asyncio.create_task(reader.list_projects())
        await asyncio.sleep(0.02)
        assert not read.done()

        scopes.release.set()
        project = await write
        listed = await read

        assert listed == (project,)
        restarted = SqliteScopeStore(database)
        assert restarted.get_project(project.id) == project

    asyncio.run(scenario())


def test_scope_cancellation_waits_for_durable_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "scope.sqlite3"
        scopes = _BlockingScopeStore(database)
        adapter = AsyncScopeStoreAdapter(scopes)
        scopes.arm()

        pending = asyncio.create_task(
            adapter.create_project(
                key="cancelled-create",
                name="Cancellation Project",
                owner_type="user",
                owner_id="user:alice",
                project_id="project_cancelled",
            )
        )
        assert await asyncio.to_thread(scopes.started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        assert not pending.done()

        scopes.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

        restarted = SqliteScopeStore(database)
        assert restarted.get_project("project_cancelled").name == "Cancellation Project"

    asyncio.run(scenario())


def test_scope_sqlite_busy_maps_to_retryable_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        scopes = _BusyScopeStore(tmp_path / "scope.sqlite3")
        adapter = AsyncScopeStoreAdapter(scopes)

        with pytest.raises(ContractError) as raised:
            await adapter.create_project(
                key="busy-create",
                name="Busy Project",
                owner_type="user",
                owner_id="user:alice",
            )

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True
        assert scopes.list_projects() == ()

    asyncio.run(scenario())


def test_scope_async_sqlite_parity_for_project_and_workspace_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "scope.sqlite3"
        scopes = SqliteScopeStore(database)
        adapter = AsyncScopeStoreAdapter(scopes)

        project = await adapter.create_project(
            key="project-create",
            name="Parity Project",
            owner_type="user",
            owner_id="user:alice",
            project_id="project_parity",
        )
        workspace = await adapter.create_workspace(
            key="workspace-create",
            project_id=project.id,
            workspace_id="workspace_parity",
        )

        assert await adapter.get_project(project.id) == project
        assert await adapter.list_projects() == (project,)
        assert await adapter.get_workspace(workspace.id) == workspace
        assert await adapter.list_workspaces() == (workspace,)

        restarted = SqliteScopeStore(database)
        assert restarted.get_project(project.id) == project
        assert restarted.get_workspace(workspace.id) == workspace

    asyncio.run(scenario())
