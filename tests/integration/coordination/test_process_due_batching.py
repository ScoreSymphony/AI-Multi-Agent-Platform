from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

from ai_multi_agent_platform.coordination.async_repository import AsyncCoordinatorRepository
from ai_multi_agent_platform.coordination.models import (
    CoordinationPhase,
    PlanCoordinationProjection,
)
from ai_multi_agent_platform.coordination.plan_step_coordinator import DurablePlanStepCoordinator


class _RuntimeRepository:
    def __init__(self, *, plan_id: str, due_at: datetime, retry_count: int) -> None:
        self.plan_id = plan_id
        self.records = tuple(
            SimpleNamespace(
                step_id=f"step_{index}",
                phase=CoordinationPhase.RETRY_SCHEDULED,
                retry_due_at=due_at,
                wait=None,
            )
            for index in range(retry_count)
        )

    async def list_active_plans(self) -> tuple[SimpleNamespace, ...]:
        return (SimpleNamespace(plan=SimpleNamespace(id=self.plan_id)),)

    async def list_step_records(self, plan_id: str) -> tuple[SimpleNamespace, ...]:
        assert plan_id == self.plan_id
        return self.records


class _CountingCoordinator(DurablePlanStepCoordinator):
    def __init__(self, *, plan_id: str, due_at: datetime, retry_count: int) -> None:
        self.runtime_repository = cast(
            AsyncCoordinatorRepository,
            _RuntimeRepository(plan_id=plan_id, due_at=due_at, retry_count=retry_count),
        )
        self.advance_calls: list[str] = []
        self.projection_calls: list[str] = []
        self._projection = PlanCoordinationProjection(
            task_id="task_retry_batch",
            plan_id=plan_id,
            plan_revision=1,
            steps=(),
        )

    async def advance(
        self,
        plan_id: str,
        *,
        now: datetime | None = None,
    ) -> PlanCoordinationProjection:
        del now
        self.advance_calls.append(plan_id)
        return self._projection

    async def async_projection(self, plan_id: str) -> PlanCoordinationProjection:
        self.projection_calls.append(plan_id)
        return self._projection


def test_process_due_advances_retry_burst_once_per_plan() -> None:
    async def run() -> None:
        due_at = datetime(2026, 9, 16, tzinfo=UTC)
        plan_id = "plan_retry_batch"
        coordinator = _CountingCoordinator(plan_id=plan_id, due_at=due_at, retry_count=3)

        projections = await coordinator.process_due(now=due_at)

        assert coordinator.advance_calls == [plan_id]
        assert coordinator.projection_calls == [plan_id]
        assert projections == (coordinator._projection,)

    asyncio.run(run())
