from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Run, RunStatus, Step, StepStatus, new_id
from ai_multi_agent_platform.kernel.models import RunState


class FailBeforeRunKernel:
    def __init__(self) -> None:
        self.fail_once = True
        self.create_calls = 0
        self.runs: dict[str, RunState] = {}
        self.by_key: dict[str, str] = {}
        self.start_keys: set[str] = set()

    async def create_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        subject_type: str = "task",
        subject_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del actor_ref, source
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated crash before canonical Run creation")
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return self.runs[existing]
        assert subject_type == "step"
        assert subject_id is not None
        run = Run(
            subject_type="step",
            subject_id=subject_id,
            owner_ref=OwnerRef(type="user", id="ready-crash-user"),
            correlation_id=task_id,
            attempt=1,
        )
        state = RunState(run=run, revision=1)
        self.runs[run.id] = state
        self.by_key[idempotency_key] = run.id
        self.create_calls += 1
        return state

    async def start_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del task_id, actor_ref, source
        current = self.runs[run_id]
        if idempotency_key in self.start_keys:
            return current
        self.start_keys.add(idempotency_key)
        running = replace(
            current,
            run=replace(current.run, status=RunStatus.RUNNING),
            revision=current.revision + 1,
        )
        self.runs[run_id] = running
        return running


def test_restart_from_persisted_ready_step_creates_exactly_one_run() -> None:
    async def scenario() -> None:
        owner = OwnerRef(type="user", id="ready-crash-user")
        plan = Plan(
            task_id=new_id("task"),
            owner_ref=owner,
            active=True,
            project_id=new_id("project"),
        )
        step = Step(
            plan_id=plan.id,
            title="ready before crash",
            owner_ref=owner,
            project_id=plan.project_id,
            status=StepStatus.READY,
        )
        record = StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=step.id,
            phase=CoordinationPhase.READY,
        )
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(plan, (step,), (record,))
        kernel = FailBeforeRunKernel()
        first = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="coordinator-a",
        )

        with pytest.raises(RuntimeError, match="before canonical Run creation"):
            await first.advance(plan.id)
        assert repository.get_step_record(step.id).phase is CoordinationPhase.READY
        assert kernel.create_calls == 0
        assert kernel.runs == {}

        restarted = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="coordinator-b",
        )
        projection = await restarted.advance(plan.id)
        assert projection.steps[0].status is StepStatus.RUNNING
        assert projection.steps[0].current_attempt == 1
        assert kernel.create_calls == 1
        assert len(kernel.runs) == 1

        await restarted.advance(plan.id)
        assert kernel.create_calls == 1
        assert len(kernel.runs) == 1

    asyncio.run(scenario())
