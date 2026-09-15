"""Regression coverage for SQLite persistence executor isolation."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol, cast

import pytest

from ai_multi_agent_platform.automation._sqlite_async import (
    AsyncSqliteOffload as AutomationSqliteOffload,
)
from ai_multi_agent_platform.data._async_offload import AsyncDataOffload
from ai_multi_agent_platform.kernel._sqlite_async import AsyncSqliteOffload as KernelSqliteOffload
from ai_multi_agent_platform.organizations._sqlite_async import (
    AsyncSqliteOffload as OrganizationSqliteOffload,
)
from ai_multi_agent_platform.repositories.async_catalog import AsyncSqliteRepositoryBindingCatalog
from ai_multi_agent_platform.repositories.catalog import SqliteRepositoryBindingCatalog
from ai_multi_agent_platform.workspaces._sqlite_async import (
    AsyncSqliteOffload as WorkspaceSqliteOffload,
)


class _Offload(Protocol):
    async def run(self, operation: Callable[[], str], *, write: bool = False) -> str: ...


_OFFLOAD_CASES: tuple[tuple[Callable[[], _Offload], str], ...] = (
    (lambda: cast(_Offload, AsyncDataOffload(max_concurrency=1)), "data-persistence"),
    (lambda: cast(_Offload, KernelSqliteOffload(max_concurrency=1)), "kernel-sqlite-persistence"),
    (
        lambda: cast(_Offload, AutomationSqliteOffload(max_concurrency=1)),
        "automation-sqlite-persistence",
    ),
    (
        lambda: cast(_Offload, OrganizationSqliteOffload(max_concurrency=1)),
        "organization-sqlite-persistence",
    ),
    (
        lambda: cast(_Offload, WorkspaceSqliteOffload(max_concurrency=1)),
        "workspace-sqlite-persistence",
    ),
)


@pytest.mark.parametrize(("factory", "thread_prefix"), _OFFLOAD_CASES)
def test_sqlite_offloads_do_not_consume_default_executor(
    factory: Callable[[], _Offload],
    thread_prefix: str,
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="regression-default")
        )
        offload = factory()
        started = threading.Event()
        release = threading.Event()

        def blocked() -> str:
            started.set()
            if not release.wait(timeout=3):
                raise TimeoutError("SQLite isolation worker release timed out")
            return threading.current_thread().name

        pending = asyncio.create_task(offload.run(blocked, write=True))
        while not started.is_set():
            await asyncio.sleep(0)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(lambda: threading.current_thread().name),
                timeout=0.5,
            )
            assert default_worker.startswith("regression-default")
            assert not pending.done()
        finally:
            release.set()

        persistence_worker = await pending
        assert persistence_worker.startswith(thread_prefix)

    asyncio.run(scenario())


class _CatalogProbe:
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        self.started = started
        self.release = release
        self.worker_name: str | None = None

    def list(self, *, connection_id: str | None = None) -> tuple[()]:
        del connection_id
        self.worker_name = threading.current_thread().name
        self.started.set()
        if not self.release.wait(timeout=3):
            raise TimeoutError("Repository catalog worker release timed out")
        return ()


def test_repository_catalog_does_not_consume_default_executor() -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="regression-default")
        )
        started = threading.Event()
        release = threading.Event()
        probe = _CatalogProbe(started, release)
        adapter = AsyncSqliteRepositoryBindingCatalog(
            cast(SqliteRepositoryBindingCatalog, probe),
            max_concurrency=1,
        )

        pending = asyncio.create_task(adapter.list())
        while not started.is_set():
            await asyncio.sleep(0)

        try:
            default_worker = await asyncio.wait_for(
                asyncio.to_thread(lambda: threading.current_thread().name),
                timeout=0.5,
            )
            assert default_worker.startswith("regression-default")
            assert not pending.done()
        finally:
            release.set()

        assert await pending == ()
        assert probe.worker_name is not None
        assert probe.worker_name.startswith("repository-catalog-persistence")

    asyncio.run(scenario())
