"""Optional HA gates for autonomous runtimes and distributed dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol

from ai_multi_agent_platform.automation.runtime import AutomationRuntimeTick
from ai_multi_agent_platform.distributed.models import WorkerJobRequest
from ai_multi_agent_platform.distributed.registry import DistributedRegistry
from ai_multi_agent_platform.distributed.runtime import DispatchRecord, DistributedRuntime
from ai_multi_agent_platform.distributed.scheduler import ScheduledPlacement

from .contracts import AuthorityGrant

AuthorityCheck = Callable[[], Awaitable[AuthorityGrant]]


class AutomationTickRunner(Protocol):
    async def run_once(self, *, now: datetime | None = None) -> AutomationRuntimeTick: ...


class AuthorityGatedAutomationLoop:
    """Run Automation ticks only while the Control Plane proves current authority."""

    def __init__(
        self,
        runtime: AutomationTickRunner,
        authority_check: AuthorityCheck,
        *,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._runtime = runtime
        self._authority_check = authority_check
        self._poll_interval_seconds = poll_interval_seconds
        self._stop_event = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self._last_error: Exception | None = None

    @property
    def running(self) -> bool:
        return self._runner is not None and not self._runner.done()

    @property
    def last_error(self) -> Exception | None:
        return self._last_error

    async def run_once(self, *, now: datetime | None = None) -> AutomationRuntimeTick:
        await self._authority_check()
        result = await self._runtime.run_once(now=now)
        self._last_error = None
        return result

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._runner = asyncio.create_task(self._run_loop(), name="ha-automation-runtime")

    async def stop(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._stop_event.set()
        await runner
        self._runner = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                self._last_error = exc

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                pass


class AuthorityGatedDistributedRuntime(DistributedRuntime):
    """Distributed runtime that fences control-side authority before dispatch side effects.

    This prevents a standby or known-stale Control Plane from creating a new reservation or
    dispatch. A second check immediately before the base dispatch boundary closes the scheduling
    race and releases the just-created reservation if authority was lost in between.

    Worker-side validation of the Control Plane fencing epoch is intentionally a separate #89
    integration step; this class does not claim that transport-level protection yet.
    """

    def __init__(
        self,
        registry: DistributedRegistry,
        *,
        authority_check: AuthorityCheck,
        **kwargs: Any,
    ) -> None:
        self._authority_check = authority_check
        super().__init__(registry, **kwargs)

    async def dispatch(
        self,
        job: WorkerJobRequest,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        await self._authority_check()
        return await super().dispatch(job, now=now)

    async def dispatch_to_worker(
        self,
        job: WorkerJobRequest,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        await self._authority_check()
        return await super().dispatch_to_worker(job, worker_id, now=now)

    async def fence_for_failover(
        self,
        worker_job_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        await self._authority_check()
        return await super().fence_for_failover(worker_job_id, now=now)

    async def redispatch_fenced(
        self,
        worker_job_id: str,
        *,
        now: datetime | None = None,
    ) -> DispatchRecord:
        await self._authority_check()
        return await super().redispatch_fenced(worker_job_id, now=now)

    async def cancel(self, worker_job_id: str) -> DispatchRecord:
        await self._authority_check()
        return await super().cancel(worker_job_id)

    async def _dispatch_placement(
        self,
        job: WorkerJobRequest,
        placement: ScheduledPlacement,
        *,
        timestamp: datetime,
    ) -> DispatchRecord:
        try:
            await self._authority_check()
        except Exception:
            self.registry.release_reservation(placement.reservation.reservation_id)
            self._persist()
            raise
        return await super()._dispatch_placement(job, placement, timestamp=timestamp)
