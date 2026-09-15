"""System-level acceptance coverage for async runtime responsiveness under SQLite pressure."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import bootstrap_standard_agents
from ai_multi_agent_platform.control_plane import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.repositories.async_catalog import AsyncSqliteRepositoryBindingCatalog
from ai_multi_agent_platform.repositories.catalog import (
    RepositoryBindingRecord,
    SqliteRepositoryBindingCatalog,
)


def _context(user_id: str, suffix: str) -> RequestContext:
    return RequestContext(
        request_id=f"request:sqlite-acceptance:{suffix}",
        correlation_id=f"correlation:sqlite-acceptance:{suffix}",
        actor=ActorContext(
            principal_ref=user_id,
            owner_type="user",
            owner_id=user_id,
            actor_type="human",
        ),
    )


async def _wait_for_thread_event(event: threading.Event, *, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not event.is_set():
        if loop.time() >= deadline:
            raise TimeoutError("SQLite acceptance worker did not start")
        await asyncio.sleep(0.001)


def test_mixed_control_plane_agent_workload_remains_responsive_during_sqlite_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep real SQLite work active while representative async platform traffic progresses."""

    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "sqlite-runtime-acceptance", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", "correct horse battery staple")
        bootstrap_standard_agents(deployment.agents)

        runtime_catalog = deployment.repository_management._catalog  # noqa: SLF001
        assert isinstance(runtime_catalog, AsyncSqliteRepositoryBindingCatalog)
        sqlite_catalog = runtime_catalog._catalog  # noqa: SLF001
        assert isinstance(sqlite_catalog, SqliteRepositoryBindingCatalog)

        started = threading.Event()
        release = threading.Event()
        worker_names: list[str] = []
        original_list = sqlite_catalog.list

        def pressure_list(
            *,
            connection_id: str | None = None,
        ) -> tuple[RepositoryBindingRecord, ...]:
            worker_names.append(threading.current_thread().name)
            with sqlite_catalog._connect() as connection:  # noqa: SLF001
                # Hold a real SQLite transaction open so the concurrent workload is measured
                # while persistence is genuinely active, not merely while a synthetic worker
                # sleeps before touching the database.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("SELECT COUNT(*) FROM repository_bindings").fetchone()
                started.set()
                if not release.wait(timeout=3.0):
                    connection.rollback()
                    raise TimeoutError("SQLite acceptance pressure release timed out")
                connection.rollback()
            return original_list(connection_id=connection_id)

        monkeypatch.setattr(sqlite_catalog, "list", pressure_list)
        blocked_persistence = asyncio.create_task(runtime_catalog.list())
        await _wait_for_thread_event(started)

        heartbeat_ticks = 0

        async def heartbeat() -> None:
            nonlocal heartbeat_ticks
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 0.15
            while loop.time() < deadline:
                heartbeat_ticks += 1
                await asyncio.sleep(0)

        async def representative_workload():
            task_pages = []
            agent_pages = []
            for index in range(6):
                task_page, agent_page = await asyncio.gather(
                    deployment.control_plane.list_tasks(
                        _context(admin.user_id, f"tasks-{index}"),
                        PageQuery(limit=20),
                    ),
                    deployment.control_plane.list_extension_resources(
                        _context(admin.user_id, f"agents-{index}"),
                        "agents",
                        PageQuery(limit=20),
                    ),
                )
                task_pages.append(task_page)
                agent_pages.append(agent_page)
            return task_pages, agent_pages

        try:
            _, (task_pages, agent_pages) = await asyncio.wait_for(
                asyncio.gather(heartbeat(), representative_workload()),
                timeout=1.5,
            )
            assert not blocked_persistence.done()
        finally:
            release.set()

        await blocked_persistence

        assert heartbeat_ticks > 10
        assert worker_names
        assert worker_names[0].startswith("repository-catalog-persistence")
        assert len(task_pages) == 6
        assert len(agent_pages) == 6
        assert all(page.get("items") for page in agent_pages)

    asyncio.run(scenario())
