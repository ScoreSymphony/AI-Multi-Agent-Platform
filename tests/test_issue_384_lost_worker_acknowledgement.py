from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
)
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Plan,
    Run,
    RunStatus,
    Step,
    StepStatus,
    Task,
    TaskStatus,
    new_id,
)
from ai_multi_agent_platform.kernel.models import (
    RecoveryDisposition,
    RecoveryEntry,
    RecoveryReport,
    RunState,
    TaskState,
)


class LostAcknowledgementKernel:
    """Model #14 recovery owning an ambiguous/lost worker acknowledgement."""

    def __init__(self, plan: Plan, step: Step) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="lost worker acknowledgement",
                owner_ref=plan.owner_ref,
                project_id=plan.project_id,
                status=TaskStatus.READY,
            ),
            revision=1,
            plan_ref=plan.id,
            step_ids=(step.id,),
        )
        self.runs: dict[str, RunState] = {}
        self.run_by_create_key: dict[str, str] = {}
        self.start_keys: set[str] = set()
        self.create_calls = 0
        self.start_calls = 0
        self.recover_calls = 0

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.task.task_id
        return self.runs[run_id]

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
        assert task_id == self.task.task_id
        existing = self.run_by_create_key.get(idempotency_key)
        if existing is not None:
            return self.runs[existing]
        assert subject_type == "step"
        assert subject_id is not None
        run = Run(
            subject_type="step",
            subject_id=subject_id,
            owner_ref=self.task.task.owner_ref,
            correlation_id=task_id,
            attempt=1,
            project_id=self.task.task.project_id,
        )
        state = RunState(run=run, revision=1)
        self.runs[run.id] = state
        self.run_by_create_key[idempotency_key] = run.id
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
        del actor_ref, source
        assert task_id == self.task.task_id
        current = self.runs[run_id]
        if idempotency_key in self.start_keys:
            return current
        self.start_keys.add(idempotency_key)
        self.start_calls += 1
        running = replace(
            current,
            run=replace(current.run, status=RunStatus.RUNNING),
            revision=current.revision + 1,
        )
        self.runs[run_id] = running
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.RUNNING),
            revision=self.task.revision + 1,
        )
        return running

    async def recover_task(self, task_id: str) -> RecoveryReport:
        assert task_id == self.task.task_id
        self.recover_calls += 1
        run_id = next(iter(self.runs))
        current = self.runs[run_id]
        self.runs[run_id] = replace(
            current,
            recovery_required=True,
            recovery_reason="worker acknowledgement lost; #14 reconciliation required",
        )
        return RecoveryReport(
            task_id=task_id,
            entries=(
                RecoveryEntry(
                    run_id=run_id,
                    before=RunStatus.RUNNING,
                    after=RunStatus.RUNNING,
                    disposition=RecoveryDisposition.ORPHANED_RECONCILIATION_REQUIRED,
                ),
            ),
        )

    async def complete_task(self, **_: object) -> TaskState:
        return self.task

    async def fail_task(self, **_: object) -> TaskState:
        return self.task

    async def cancel_task(self, **_: object) -> TaskState:
        return self.task

    async def cancel_run(self, **_: object) -> RunState:
        raise AssertionError("lost-ack reconciliation must not cancel the canonical Run")


def test_lost_worker_acknowledgement_delegates_to_kernel_without_blind_redispatch() -> None:
    async def scenario() -> None:
        owner = OwnerRef(type="user", id="issue-384-user")
        plan = Plan(
            task_id=new_id("task"),
            owner_ref=owner,
            active=True,
            project_id=new_id("project"),
        )
        step = Step(
            id=new_id("step"),
            plan_id=plan.id,
            title="worker-owned execution",
            owner_ref=owner,
            project_id=plan.project_id,
        )
        repository = InMemoryCoordinatorRepository()
        kernel = LostAcknowledgementKernel(plan, step)
        coordinator = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="issue-384-coordinator",
        )

        projection = await coordinator.register_plan(plan, (step,))
        original_run_id = projection.steps[0].latest_run_id
        assert original_run_id is not None
        assert projection.steps[0].status is StepStatus.RUNNING
        assert projection.steps[0].phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert kernel.create_calls == 1
        assert kernel.start_calls == 1

        first = await coordinator.reconcile_plan(plan.id)
        second = await coordinator.reconcile_plan(plan.id)

        assert first.steps[0].latest_run_id == original_run_id
        assert second.steps[0].latest_run_id == original_run_id
        assert second.steps[0].status is StepStatus.RUNNING
        assert second.steps[0].phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert kernel.recover_calls == 2
        assert kernel.create_calls == 1
        assert kernel.start_calls == 1
        assert kernel.runs[original_run_id].recovery_required is True

    asyncio.run(scenario())
