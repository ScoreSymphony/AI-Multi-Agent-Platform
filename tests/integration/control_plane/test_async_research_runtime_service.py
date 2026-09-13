"""Research runtime composition regressions for issue #892."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.research import (
    AsyncResearchService,
    ResearchClass,
    ResearchService,
    SqliteResearchRepository,
    SynchronousResearchService,
)


class _SlowResearchRepository(SqliteResearchRepository):
    def __init__(self, path: Path) -> None:
        self.delay_connections = False
        self.runtime_connection_threads: list[int] = []
        super().__init__(path)
        self.delay_connections = True

    def _connect(self) -> sqlite3.Connection:
        if self.delay_connections:
            self.runtime_connection_threads.append(threading.get_ident())
            time.sleep(0.05)
        return super()._connect()


def test_package_research_service_is_runtime_safe_composition(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "research.sqlite3"
        repository = _SlowResearchRepository(database)
        service = ResearchService(repository)
        event_loop_thread = threading.get_ident()

        assert isinstance(service, AsyncResearchService)
        assert isinstance(service, SynchronousResearchService)

        create = asyncio.create_task(
            service.create_item(
                title="Runtime Research",
                question="Is package-level Research persistence awaitable?",
                research_class=ResearchClass.PROJECT_RESEARCH,
                owner_ref=OwnerRef(type="user", id="issue-892-runtime"),
            )
        )
        await asyncio.sleep(0.01)
        assert not create.done()

        item = await create
        assert repository.runtime_connection_threads
        assert all(
            thread_id != event_loop_thread
            for thread_id in repository.runtime_connection_threads
        )

        restarted = SqliteResearchRepository(database)
        assert restarted.get_item(item.research_item_id) == item

    asyncio.run(scenario())
