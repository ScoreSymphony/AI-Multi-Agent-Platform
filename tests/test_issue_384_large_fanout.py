from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import cast

from ai_multi_agent_platform.coordination import DurablePlanStepCoordinator, InMemoryCoordinatorRepository
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
from ai_multi_agent_platform.kernel.models import RecoveryReport, RunState, TaskState


class FanoutKernel:
    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="large deterministic fan-out",
                owner_ref=plan.owner_ref,
                project_id=plan.project_id,
                status=TaskStatus.READY,
            ),
            revision=1,
            plan_ref=plan.id,
            step_ids=tuple(step.id for step in steps),
        )
        self.runs: dict[str, RunState] = {}
        self.by_key: dict[str, str] = {}
        self.start_keys: set[str] = set()
        self.create_calls = 0

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
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return self.runs[existing]
        assert subject_type == "step"
        assert subject_id is not None
        attempt = 1 + sum(
            1
            for state in self.runs.values()
            if state.run.subject_type == "step" and state.run.subject_id == subject_id
        )
        run = Run(
            subject_type="step",
            subject_id=subject_id,
            owner_ref=self.task.task.owner_ref,
            correlation_id=task_id,
            attempt=attempt,
            project_id=self.task.task.project_id,
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
        del actor_ref, source
        assert task_id == self.task.task_id
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
        if self.task.status is TaskStatus.READY:
            self.task = replace(
                self.task,
                task=replace(self.task.task, status=TaskStatus.RUNNING),
                revision=self.task.revision + 1,
            )
        return running

    async def cancel_run(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        run_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> RunState:
        del idempotency_key, task_id, actor_ref, source
        current = self.runs[run_id]
        cancelled = replace(
            current,
            run=replace(current.run, status=RunStatus.CANCELLED),
            revision=current.revision + 1,
        )
        self.runs[run_id] = cancelled
        return cancelled

    async def complete_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.SUCCEEDED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def fail_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        reason: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, reason, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.FAILED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def cancel_task(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        del idempotency_key, actor_ref, source
        assert task_id == self.task.task_id
        self.task = replace(
            self.task,
            task=replace(self.task.task, status=TaskStatus.CANCELLED),
            revision=self.task.revision + 1,
        )
        return self.task

    async def recover_task(self, task_id: str) -> RecoveryReport:
        assert task_id == self.task.task_id
        return RecoveryReport(task_id=task_id, entries=())

    def succeed(self, run_id: str) -> None:
        current = self.runs[run_id]
        self.runs[run_id] = replace(
            current,
            run=replace(current.run, status=RunStatus.SUCCEEDED),
            revision=current.revision + 1,
        )


def test_large_deterministic_fan_out_fan_in_activates_barrier_once() -> None:
    async def scenario() -> None:
        leaf_count = 64
        owner = OwnerRef(type="user", id="fanout-regression-user")
        plan = Plan(
            task_id=new_id("task"),
            owner_ref=owner,
            active=True,
            project_id=new_id("project"),
        )
        root_id = new_id("step")
        leaf_ids = tuple(new_id("step") for _ in range(leaf_count))
        barrier_id = new_id("step")
        root = Step(
            id=root_id,
            plan_id=plan.id,
            title="root",
            owner_ref=owner,
            project_id=plan.project_id,
        )
        leaves = tuple(
            Step(
                id=step_id,
                plan_id=plan.id,
                title=f"leaf-{index:02d}",
                owner_ref=owner,
                project_id=plan.project_id,
                depends_on=(root_id,),
            )
            for index, step_id in enumerate(leaf_ids)
        )
        barrier = Step(
            id=barrier_id,
            plan_id=plan.id,
            title="fan-in barrier",
            owner_ref=owner,
            project_id=plan.project_id,
            depends_on=leaf_ids,
        )
        steps = (root, *leaves, barrier)
        kernel = FanoutKernel(plan, steps)
        coordinator = DurablePlanStepCoordinator(
            repository=InMemoryCoordinatorRepository(),
            kernel=kernel,
            coordinator_id="fanout-coordinator",
        )

        projection = await coordinator.register_plan(plan, steps)
        assert kernel.create_calls == 1
        root_run = cast(str, projection.steps[0].latest_run_id)
        kernel.succeed(root_run)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=root_run)
        by_id = {item.step_id: item for item in projection.steps}
        assert all(by_id[step_id].status is StepStatus.RUNNING for step_id in leaf_ids)
        assert by_id[barrier_id].status is StepStatus.PENDING
        assert kernel.create_calls == leaf_count + 1

        leaf_runs = {step_id: cast(str, by_id[step_id].latest_run_id) for step_id in leaf_ids}
        for index, step_id in enumerate(reversed(leaf_ids)):
            run_id = leaf_runs[step_id]
            kernel.succeed(run_id)
            projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_id)
            barrier_projection = {item.step_id: item for item in projection.steps}[barrier_id]
            if index < leaf_count - 1:
                assert barrier_projection.status is StepStatus.PENDING
                assert kernel.create_calls == leaf_count + 1

        by_id = {item.step_id: item for item in projection.steps}
        barrier_projection = by_id[barrier_id]
        assert barrier_projection.status is StepStatus.RUNNING
        assert len(barrier_projection.satisfied_dependency_ids) == leaf_count
        assert set(barrier_projection.satisfied_dependency_ids) == set(leaf_ids)
        assert kernel.create_calls == leaf_count + 2

        duplicate_leaf_run = leaf_runs[leaf_ids[0]]
        await coordinator.observe_run(task_id=plan.task_id, run_id=duplicate_leaf_run)
        assert kernel.create_calls == leaf_count + 2

        barrier_run = cast(str, barrier_projection.latest_run_id)
        kernel.succeed(barrier_run)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=barrier_run)
        assert all(item.status is StepStatus.SUCCEEDED for item in projection.steps)
        assert kernel.task.status is TaskStatus.SUCCEEDED
        assert kernel.create_calls == leaf_count + 2

    asyncio.run(scenario())
