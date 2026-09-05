from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from ai_multi_agent_platform.automation.runtime import AutomationRuntimeTick
from ai_multi_agent_platform.distributed import DistributedRegistry
from ai_multi_agent_platform.distributed.models import WorkerJobRequest
from ai_multi_agent_platform.high_availability import (
    AvailabilityMode,
    ControlPlaneFailoverService,
    InMemoryCoordinationProvider,
    NotLeaderError,
    StaleFencingToken,
)
from ai_multi_agent_platform.high_availability.integrations import (
    AuthorityGatedAutomationLoop,
    AuthorityGatedDistributedRuntime,
)

NOW = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)
TTL = timedelta(seconds=5)


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


@dataclass
class RecordingAutomationRuntime:
    calls: int = 0

    async def run_once(self, *, now: datetime | None = None) -> AutomationRuntimeTick:
        del now
        self.calls += 1
        return AutomationRuntimeTick()


class CountingScheduler:
    def __init__(self) -> None:
        self.calls = 0

    def schedule(self, job: WorkerJobRequest, *, now: datetime | None = None) -> Any:
        del job, now
        self.calls += 1
        raise RuntimeError("scheduler reached")


def test_automation_ticks_follow_current_leadership() -> None:
    async def scenario() -> None:
        clock = MutableClock()
        coordinator = InMemoryCoordinationProvider(clock=clock)
        first = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        second = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        runtime = RecordingAutomationRuntime()
        gated = AuthorityGatedAutomationLoop(runtime, first.require_authority)

        assert await first.start() is True
        assert await second.start() is False
        await gated.run_once(now=NOW)
        assert runtime.calls == 1

        clock.advance(TTL + timedelta(milliseconds=1))
        assert await second.try_promote(reason="failover") is True
        with pytest.raises(StaleFencingToken):
            await gated.run_once(now=clock.value)
        assert runtime.calls == 1

        second_runtime = RecordingAutomationRuntime()
        second_gated = AuthorityGatedAutomationLoop(second_runtime, second.require_authority)
        await second_gated.run_once(now=clock.value)
        assert second_runtime.calls == 1

    asyncio.run(scenario())


def test_standby_distributed_runtime_rejects_before_scheduling() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider()
        active = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        standby = ControlPlaneFailoverService(
            instance_id="control-b",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await active.start() is True
        assert await standby.start() is False

        scheduler = CountingScheduler()
        runtime = AuthorityGatedDistributedRuntime(
            DistributedRegistry(),
            authority_check=standby.require_authority,
            scheduler=cast(Any, scheduler),
        )
        fake_job = cast(WorkerJobRequest, object())
        with pytest.raises(NotLeaderError):
            await runtime.dispatch(fake_job, now=NOW)
        assert scheduler.calls == 0

    asyncio.run(scenario())


def test_active_distributed_runtime_reaches_scheduler_after_authority_check() -> None:
    async def scenario() -> None:
        coordinator = InMemoryCoordinationProvider()
        active = ControlPlaneFailoverService(
            instance_id="control-a",
            mode=AvailabilityMode.ACTIVE_PASSIVE,
            coordinator=coordinator,
            lease_ttl=TTL,
        )
        assert await active.start() is True

        scheduler = CountingScheduler()
        runtime = AuthorityGatedDistributedRuntime(
            DistributedRegistry(),
            authority_check=active.require_authority,
            scheduler=cast(Any, scheduler),
        )
        fake_job = cast(WorkerJobRequest, object())
        with pytest.raises(RuntimeError, match="scheduler reached"):
            await runtime.dispatch(fake_job, now=NOW)
        assert scheduler.calls == 1

    asyncio.run(scenario())
