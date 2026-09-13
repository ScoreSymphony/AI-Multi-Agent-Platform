from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    CoordinatorClaim,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    SQLiteCoordinatorRepository,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitType,
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
from ai_multi_agent_platform.kernel.models import RecoveryReport, RunState, TaskState


class DurableFakeKernel:
    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="durability",
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
        self.cancel_keys: set[str] = set()
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
        if idempotency_key in self.start_keys:
            return self.runs[run_id]
        self.start_keys.add(idempotency_key)
        current = await self.get_run(task_id, run_id)
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
        del actor_ref, source
        if idempotency_key in self.cancel_keys:
            return self.runs[run_id]
        self.cancel_keys.add(idempotency_key)
        current = await self.get_run(task_id, run_id)
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

    def finish(self, run_id: str, status: RunStatus) -> None:
        current = self.runs[run_id]
        self.runs[run_id] = replace(
            current,
            run=replace(current.run, status=status),
            revision=current.revision + 1,
        )


def _plan_steps(
    dependencies: tuple[tuple[int, ...], ...],
) -> tuple[Plan, tuple[Step, ...]]:
    owner = OwnerRef(type="user", id="durability-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    ids = tuple(new_id("step") for _ in dependencies)
    steps = tuple(
        Step(
            id=ids[index],
            plan_id=plan.id,
            title=f"step-{index}",
            owner_ref=owner,
            project_id=plan.project_id,
            depends_on=tuple(ids[item] for item in predecessors),
        )
        for index, predecessors in enumerate(dependencies)
    )
    return plan, steps


class FailOnceRepository(InMemoryCoordinatorRepository):
    def __init__(self) -> None:
        super().__init__()
        self.fail_active_commit = True

    def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord:
        if self.fail_active_commit and record.phase is CoordinationPhase.ATTEMPT_ACTIVE:
            self.fail_active_commit = False
            raise RuntimeError("simulated crash after canonical Run creation")
        return super().save_step(
            step=step,
            record=record,
            expected_revision=expected_revision,
            claim=claim,
            now=now,
        )


def test_crash_after_run_creation_before_coordinator_commit_is_exactly_once() -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((),))
        store = FailOnceRepository()
        kernel = DurableFakeKernel(plan, steps)
        coordinator = DurablePlanStepCoordinator(
            repository=store,
            kernel=kernel,
            coordinator_id="coordinator-a",
        )
        with pytest.raises(RuntimeError, match="canonical Run creation"):
            await coordinator.register_plan(plan, steps)
        assert kernel.create_calls == 1
        assert store.get_step_record(steps[0].id).phase is CoordinationPhase.READY

        restarted = DurablePlanStepCoordinator(
            repository=store,
            kernel=kernel,
            coordinator_id="coordinator-b",
        )
        projection = await restarted.advance(plan.id)
        assert projection.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 1
        assert len(kernel.runs) == 1

    asyncio.run(scenario())


def test_sqlite_deadline_wait_survives_restart_and_resumes_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((),))
        path = tmp_path / "coordination.sqlite3"
        kernel = DurableFakeKernel(plan, steps)
        first = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-a",
        )
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        await first.register_plan(plan, steps)
        await first.wait_step(
            StepWait(
                wait_key="deadline-1",
                wait_type=WaitType.DEADLINE,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[0].id,
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
                deadline_at=t0 + timedelta(minutes=5),
            ),
            now=t0,
        )

        restarted = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-b",
        )
        assert restarted.projection(plan.id).steps[0].status is StepStatus.WAITING
        await restarted.process_due(now=t0 + timedelta(minutes=5))
        assert restarted.projection(plan.id).steps[0].status is StepStatus.RUNNING
        await restarted.process_due(now=t0 + timedelta(minutes=6))
        assert kernel.create_calls == 1

    asyncio.run(scenario())


def test_sqlite_retry_deadline_survives_restart_and_fires_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((),))
        path = tmp_path / "coordination.sqlite3"
        kernel = DurableFakeKernel(plan, steps)
        first = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-a",
        )
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        projection = await first.register_plan(
            plan,
            steps,
            retry_policies={
                steps[0].id: StepRetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=30,
                    retryable_categories=("transient",),
                )
            },
        )
        run_id = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_id, RunStatus.FAILED)
        await first.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            failure_category="transient",
            now=t0,
        )

        restarted = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-b",
        )
        await restarted.process_due(now=t0 + timedelta(seconds=30))
        projection = restarted.projection(plan.id)
        assert projection.steps[0].current_attempt == 2
        assert projection.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 2
        await restarted.process_due(now=t0 + timedelta(minutes=1))
        assert kernel.create_calls == 2

    asyncio.run(scenario())


def test_sqlite_partial_fan_in_survives_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((), (), (0, 1)))
        path = tmp_path / "coordination.sqlite3"
        kernel = DurableFakeKernel(plan, steps)
        first = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-a",
        )
        projection = await first.register_plan(plan, steps)
        by_id = {item.step_id: item for item in projection.steps}
        run_a = cast(str, by_id[steps[0].id].latest_run_id)
        run_b = cast(str, by_id[steps[1].id].latest_run_id)
        kernel.finish(run_a, RunStatus.SUCCEEDED)
        projection = await first.observe_run(task_id=plan.task_id, run_id=run_a)
        barrier = {item.step_id: item for item in projection.steps}[steps[2].id]
        assert barrier.satisfied_dependency_ids == (steps[0].id,)
        assert barrier.status is StepStatus.PENDING

        restarted = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(path),
            kernel=kernel,
            coordinator_id="coordinator-b",
        )
        kernel.finish(run_b, RunStatus.SUCCEEDED)
        projection = await restarted.observe_run(task_id=plan.task_id, run_id=run_b)
        barrier = {item.step_id: item for item in projection.steps}[steps[2].id]
        assert set(barrier.satisfied_dependency_ids) == {steps[0].id, steps[1].id}
        assert barrier.status is StepStatus.RUNNING
        assert kernel.create_calls == 3

    asyncio.run(scenario())


def test_sqlite_stale_fence_cannot_commit_after_takeover(tmp_path: Path) -> None:
    plan, steps = _plan_steps(((),))
    record = StepCoordinationRecord(
        task_id=plan.task_id,
        plan_id=plan.id,
        plan_revision=plan.revision,
        step_id=steps[0].id,
        phase=CoordinationPhase.BLOCKED,
        dependency_ids=(),
    )
    store = SQLiteCoordinatorRepository(tmp_path / "coordination.sqlite3")
    store.create_plan(plan, steps, (record,))
    t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    stale = store.acquire_claim(
        step_id=steps[0].id,
        owner_id="coordinator-a",
        ttl=timedelta(seconds=5),
        now=t0,
    )
    assert stale is not None
    current = store.acquire_claim(
        step_id=steps[0].id,
        owner_id="coordinator-b",
        ttl=timedelta(seconds=30),
        now=t0 + timedelta(seconds=6),
    )
    assert current is not None
    assert current.fence > stale.fence

    with pytest.raises(ContractError, match="stale or expired"):
        store.save_step(
            step=steps[0],
            record=replace(record, phase=CoordinationPhase.READY),
            expected_revision=record.revision,
            claim=stale,
            now=t0 + timedelta(seconds=6),
        )
