from __future__ import annotations

import asyncio
import multiprocessing
import os
import time
import uuid
from datetime import timedelta

import pytest

from ai_multi_agent_platform.distributed.postgres_control_plane_coordination import (
    PostgresCoordinationProvider,
)
from ai_multi_agent_platform.high_availability import FencingToken, StaleFencingToken

_DSN_ENV = "AI_PLATFORM_TEST_HA_POSTGRES_DSN"


def _acquire_in_process(
    dsn: str,
    lease_name: str,
    instance_id: str,
    ttl_seconds: float,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    async def scenario() -> tuple[str, int | None]:
        from ai_multi_agent_platform.high_availability import LeadershipConflict

        provider = PostgresCoordinationProvider(dsn, lease_name=lease_name)
        start.wait(timeout=10)
        try:
            lease = await provider.acquire(
                instance_id,
                ttl=timedelta(seconds=ttl_seconds),
            )
        except LeadershipConflict:
            return "conflict", None
        return "leader", lease.token.epoch

    results.put((instance_id, *asyncio.run(scenario())))


@pytest.mark.integration
def test_real_postgres_coordinates_independent_processes_and_fences_old_epoch() -> None:
    dsn = os.environ.get(_DSN_ENV)
    if not dsn:
        pytest.skip(f"set {_DSN_ENV} to run the real PostgreSQL HA coordination test")
    pytest.importorskip("psycopg")

    lease_name = f"issue-566-{uuid.uuid4().hex}"
    ttl_seconds = 1.0

    async def bootstrap() -> None:
        provider = PostgresCoordinationProvider(dsn, lease_name=lease_name)
        await provider.initialize()
        state = await provider.inspect()
        assert state.epoch == 0
        assert state.owner_instance_id is None

    asyncio.run(bootstrap())

    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_acquire_in_process,
            args=(dsn, lease_name, instance_id, ttl_seconds, start, results),
        )
        for instance_id in ("control-a", "control-b")
    ]
    for process in processes:
        process.start()
    start.set()
    observed = [results.get(timeout=15) for _ in processes]
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    leaders = [item for item in observed if item[1] == "leader"]
    conflicts = [item for item in observed if item[1] == "conflict"]
    assert len(leaders) == 1
    assert len(conflicts) == 1
    old_owner = leaders[0][0]
    assert leaders[0][2] == 1
    new_owner = "control-b" if old_owner == "control-a" else "control-a"

    time.sleep(ttl_seconds + 0.25)

    async def promote_and_fence() -> None:
        provider = PostgresCoordinationProvider(dsn, lease_name=lease_name)
        promoted = await provider.acquire(
            new_owner,
            ttl=timedelta(seconds=5),
        )
        assert promoted.token == FencingToken(instance_id=new_owner, epoch=2)
        with pytest.raises(StaleFencingToken):
            await provider.assert_fence(FencingToken(instance_id=old_owner, epoch=1))
        await provider.release(promoted.token)

    asyncio.run(promote_and_fence())
