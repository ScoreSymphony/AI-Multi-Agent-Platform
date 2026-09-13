"""Async SQLite Workspace persistence integration coverage for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBinding,
    SqliteRunWorkspaceBindingRepository,
    SqliteWorkspaceProvider,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces._sqlite_async import AsyncSqliteOffload


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-892-workspaces",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


def _files(tmp_path: Path) -> LocalFileProvider:
    return LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite3")


class _SlowWorkspaceProvider(SqliteWorkspaceProvider):
    def __init__(self, root: Path, files: LocalFileProvider, db_path: Path) -> None:
        self.delay_persistence = False
        super().__init__(root, files, db_path)
        self.delay_persistence = True

    def _persist_state(self) -> None:
        if self.delay_persistence:
            time.sleep(0.08)
        super()._persist_state()


class _RecordingWorkspaceProvider(SqliteWorkspaceProvider):
    def __init__(self, root: Path, files: LocalFileProvider, db_path: Path) -> None:
        self.connection_threads: list[int] = []
        super().__init__(root, files, db_path)
        self.connection_threads.clear()

    def _connect_workspace_db(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect_workspace_db()


class _BusyWorkspaceProvider(SqliteWorkspaceProvider):
    def __init__(self, root: Path, files: LocalFileProvider, db_path: Path) -> None:
        self.raise_busy = False
        super().__init__(root, files, db_path)
        self.raise_busy = True

    def _connect_workspace_db(self) -> sqlite3.Connection:
        if self.raise_busy:
            raise sqlite3.OperationalError("database is locked")
        return super()._connect_workspace_db()


class _RecordingBindingRepository(SqliteRunWorkspaceBindingRepository):
    def __init__(self, db_path: Path) -> None:
        self.connection_threads: list[int] = []
        super().__init__(db_path)
        self.connection_threads.clear()

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.get_ident())
        return super()._connect()


class _BusyBindingRepository(SqliteRunWorkspaceBindingRepository):
    def __init__(self, db_path: Path) -> None:
        self.raise_busy = False
        super().__init__(db_path)
        self.raise_busy = True

    def _connect(self) -> sqlite3.Connection:
        if self.raise_busy:
            raise sqlite3.OperationalError("database is busy")
        return super()._connect()


def _binding() -> RunWorkspaceBinding:
    return RunWorkspaceBinding(
        run_id=new_id("run"),
        task_id=new_id("task"),
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        content_checksum="a" * 64,
    )


def test_workspace_sqlite_checkpoint_does_not_block_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        provider = _SlowWorkspaceProvider(
            tmp_path / "materializations",
            _files(tmp_path),
            tmp_path / "workspaces.sqlite3",
        )
        pending = asyncio.create_task(
            provider.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=_context(project_id),
            )
        )

        await asyncio.sleep(0.01)

        assert not pending.done()
        workspace = await pending
        assert workspace.project_id == project_id

    asyncio.run(scenario())


def test_workspace_sqlite_connections_are_opened_in_worker_threads(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        provider = _RecordingWorkspaceProvider(
            tmp_path / "materializations",
            _files(tmp_path),
            tmp_path / "workspaces.sqlite3",
        )
        event_loop_thread = threading.get_ident()

        await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=_context(project_id),
        )

        assert provider.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in provider.connection_threads)

    asyncio.run(scenario())


def test_workspace_sqlite_busy_is_retryable_and_restores_checkpoint(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        workspace_id = new_id("workspace")
        provider = _BusyWorkspaceProvider(
            tmp_path / "materializations",
            _files(tmp_path),
            tmp_path / "workspaces.sqlite3",
        )

        with pytest.raises(ContractError) as raised:
            await provider.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=_context(project_id),
                workspace_id=workspace_id,
            )

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True
        with pytest.raises(ContractError) as missing:
            await provider.get_workspace(workspace_id)
        assert missing.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_run_workspace_binding_sqlite_uses_worker_thread_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "run-workspace-bindings.sqlite3"
        repository = _RecordingBindingRepository(database)
        binding = _binding()
        event_loop_thread = threading.get_ident()

        assert await repository.bind(binding) == binding
        assert await repository.get(binding.run_id) == binding
        assert repository.connection_threads
        assert all(thread_id != event_loop_thread for thread_id in repository.connection_threads)

        restarted = SqliteRunWorkspaceBindingRepository(database)
        assert await restarted.get(binding.run_id) == binding
        assert await restarted.bind(binding) == binding

    asyncio.run(scenario())


def test_run_workspace_binding_sqlite_maps_busy_to_retryable_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = _BusyBindingRepository(tmp_path / "run-workspace-bindings.sqlite3")

        with pytest.raises(ContractError) as raised:
            await repository.get(new_id("run"))

        assert raised.value.code is ErrorCode.TRANSIENT_FAILURE
        assert raised.value.retryable is True

    asyncio.run(scenario())


def test_workspace_sqlite_repeated_cancellation_waits_for_worker_boundary() -> None:
    async def scenario() -> None:
        offload = AsyncSqliteOffload(max_concurrency=1)
        started = threading.Event()
        release = threading.Event()

        def operation() -> None:
            started.set()
            if not release.wait(timeout=2):
                raise TimeoutError("test operation release timed out")

        pending = asyncio.create_task(offload.run(operation, write=True))
        assert await asyncio.to_thread(started.wait, 1)

        pending.cancel()
        await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending

    asyncio.run(scenario())
