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
    LocalWorkspaceProvider,
    RetentionManagedWorkspaceProvider,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceType,
)
from ai_multi_agent_platform.workspaces._retention_async import (
    WorkspaceRetentionPersistenceOffload,
)


def _context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="retention-async-persistence",
            owner_type="user",
            owner_id="workspace-user",
            project_id=project_id,
        ),
        actor_ref="user:workspace-user",
    )


async def _wait_for_event(event: threading.Event, *, timeout: float = 1.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while not event.is_set() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.005)
    return event.is_set()


class _SlowRetentionManagedWorkspaceProvider(RetentionManagedWorkspaceProvider):
    def __init__(self, delegate: WorkspaceProvider, *, metadata_db_path: Path) -> None:
        self.delay_seconds = 0.0
        self.connection_threads: list[str] = []
        super().__init__(delegate, metadata_db_path=metadata_db_path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()


def test_retention_sqlite_checkpoint_keeps_event_loop_responsive(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        files = LocalFileProvider(tmp_path / "objects", tmp_path / "files.sqlite")
        provider = _SlowRetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "materializations", files),
            metadata_db_path=tmp_path / "retention.sqlite",
        )
        provider.connection_threads.clear()
        provider.delay_seconds = 0.08

        creation = asyncio.create_task(
            provider.create_workspace(
                project_id=project_id,
                owner_ref=OwnerRef(type="user", id="workspace-user"),
                workspace_type=WorkspaceType.PERSISTENT_PROJECT,
                context=_context(project_id),
            )
        )
        heartbeat = 0
        while not creation.done():
            heartbeat += 1
            await asyncio.sleep(0.005)

        workspace = await creation
        assert workspace.id.startswith("workspace_")
        assert heartbeat >= 2
        assert provider.connection_threads
        assert all(
            name.startswith("workspace-retention-persistence")
            for name in provider.connection_threads
        )

    asyncio.run(scenario())


def test_retention_offload_bounds_pending_work_and_isolates_default_executor() -> None:
    async def scenario() -> None:
        offload = WorkspaceRetentionPersistenceOffload(max_pending=1)
        started = threading.Event()
        release = threading.Event()

        def blocking_operation() -> str:
            started.set()
            release.wait(timeout=5)
            return "retention-complete"

        first = asyncio.create_task(
            offload.run(blocking_operation, message="retention persistence failed")
        )
        assert await _wait_for_event(started)

        with pytest.raises(ContractError) as captured:
            await offload.run(lambda: "queued", message="retention persistence failed")
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

        unrelated = await asyncio.wait_for(asyncio.to_thread(lambda: "default-executor-free"), 1.0)
        assert unrelated == "default-executor-free"

        release.set()
        assert await first == "retention-complete"

    asyncio.run(scenario())


def test_retention_offload_waits_for_started_persistence_on_cancellation() -> None:
    async def scenario() -> None:
        offload = WorkspaceRetentionPersistenceOffload(max_pending=1)
        started = threading.Event()
        release = threading.Event()
        completed = threading.Event()

        def blocking_operation() -> None:
            started.set()
            release.wait(timeout=5)
            completed.set()

        persistence = asyncio.create_task(
            offload.run(blocking_operation, message="retention persistence failed")
        )
        assert await _wait_for_event(started)
        persistence.cancel()
        await asyncio.sleep(0)
        assert not persistence.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await persistence
        assert completed.is_set()

    asyncio.run(scenario())


def test_retention_offload_maps_sqlite_busy_to_retryable_transient_failure() -> None:
    async def scenario() -> None:
        offload = WorkspaceRetentionPersistenceOffload()

        def fail_locked() -> None:
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(ContractError) as captured:
            await offload.run(fail_locked, message="failed to persist retention metadata")
        assert captured.value.code is ErrorCode.TRANSIENT_FAILURE
        assert captured.value.retryable is True

    asyncio.run(scenario())


def test_retention_in_memory_and_sqlite_runtime_contracts_match(tmp_path: Path) -> None:
    async def exercise(
        provider: RetentionManagedWorkspaceProvider,
        *,
        project_id: str,
        workspace_id: str,
    ) -> tuple[WorkspaceRetention, bool]:
        workspace = await provider.create_workspace(
            project_id=project_id,
            owner_ref=OwnerRef(type="user", id="workspace-user"),
            workspace_type=WorkspaceType.PERSISTENT_PROJECT,
            context=_context(project_id),
            workspace_id=workspace_id,
        )
        updated = await provider.set_retention(workspace.id, WorkspaceRetention.EPHEMERAL)
        retained = await provider.get_workspace(workspace.id)
        return updated.retention, retained.id == workspace.id

    async def scenario() -> None:
        project_id = new_id("project")
        workspace_id = new_id("workspace")

        memory_files = LocalFileProvider(
            tmp_path / "memory-objects",
            tmp_path / "memory-files.sqlite",
        )
        sqlite_files = LocalFileProvider(
            tmp_path / "sqlite-objects",
            tmp_path / "sqlite-files.sqlite",
        )
        memory = RetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "memory-materializations", memory_files)
        )
        durable = RetentionManagedWorkspaceProvider(
            LocalWorkspaceProvider(tmp_path / "sqlite-materializations", sqlite_files),
            metadata_db_path=tmp_path / "retention.sqlite",
        )

        assert await exercise(
            memory,
            project_id=project_id,
            workspace_id=workspace_id,
        ) == await exercise(
            durable,
            project_id=project_id,
            workspace_id=workspace_id,
        )

    asyncio.run(scenario())
