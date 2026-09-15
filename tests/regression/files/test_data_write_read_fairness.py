from __future__ import annotations

import asyncio
import threading

from ai_multi_agent_platform.data._async_offload import AsyncDataOffload


def test_queued_data_writes_do_not_occupy_read_workers() -> None:
    async def scenario() -> None:
        offload = AsyncDataOffload(max_concurrency=2)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()

        def first_write() -> str:
            first_started.set()
            if not release_first.wait(timeout=3):
                raise TimeoutError("first Data write was not released")
            return "first"

        def second_write() -> str:
            second_started.set()
            return "second"

        first = asyncio.create_task(offload.run(first_write, write=True))
        while not first_started.is_set():
            await asyncio.sleep(0)

        second = asyncio.create_task(offload.run(second_write, write=True))
        await asyncio.sleep(0)
        assert not second_started.is_set()

        read_result = await asyncio.wait_for(
            offload.run(lambda: "read"),
            timeout=0.5,
        )
        assert read_result == "read"
        assert not second_started.is_set()

        release_first.set()
        assert await first == "first"
        assert await second == "second"

    asyncio.run(scenario())
