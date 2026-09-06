from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ai_multi_agent_platform.coordination import (
    ApprovalOutcome,
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    PredecessorFailurePolicy,
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


class EdgeKernel:
    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="issue 384 edge cases",
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
        self.block_create = False
        self.create_entered = asyncio.Event()
        self.release_create = asyncio.Event()

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
        if self.block_create:
            self.create_entered.set()
            await self.release_create.wait()
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
        del actor_ref, source
        assert task_id == self.task.task_id
        if idempotency_key in self.cancel_keys:
            return self.runs[run_id]
        self.cancel_keys.add(idempotency_key)
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
        if self.task.status is not TaskStatus.CANCELLED:
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
    owner = OwnerRef(type="user", id="edge-case-user")
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


def _runtime(
    dependencies: tuple[tuple[int, ...], ...] = ((),),
) -> tuple[
    Plan,
    tuple[Step, ...],
    InMemoryCoordinatorRepository,
    EdgeKernel,
    DurablePlanStepCoordinator,
]:
    plan, steps = _plan_steps(dependencies)
    repository = InMemoryCoordinatorRepository()
    kernel = EdgeKernel(plan, steps)
    coordinator = DurablePlanStepCoordinator(
        repository=repository,
        kernel=kernel,
        coordinator_id="edge-coordinator",
    )
    return plan, steps, repository, kernel, coordinator


def test_linear_dependency_chain_progresses_exactly_once() -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime(((), (0,), (1,)))
        projection = await coordinator.register_plan(plan, steps)
        for index in range(3):
            item = {step.step_id: step for step in projection.steps}[steps[index].id]
            assert item.status is StepStatus.RUNNING
            run_id = cast(str, item.latest_run_id)
            kernel.finish(run_id, RunStatus.SUCCEEDED)
            projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_id)
        assert all(item.status is StepStatus.SUCCEEDED for item in projection.steps)
        assert kernel.create_calls == 3
        assert kernel.task.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("outcome_raw", "expected_step", "expected_task"),
    [
        ("rejected", StepStatus.FAILED, TaskStatus.FAILED),
        ("expired", StepStatus.FAILED, TaskStatus.FAILED),
        ("cancelled", StepStatus.CANCELLED, TaskStatus.CANCELLED),
    ],
)
def test_approval_non_success_outcomes_are_deterministic(
    outcome_raw: str,
    expected_step: StepStatus,
    expected_task: TaskStatus,
) -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime()
        await coordinator.register_plan(plan, steps)
        await coordinator.wait_step(
            StepWait(
                wait_key=f"approval-{outcome_raw}",
                wait_type=WaitType.APPROVAL,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[0].id,
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
                approval_id="approval-edge-1",
                approval_subject_type="step",
                approval_subject_id=steps[0].id,
                approval_action="repository:write",
            )
        )
        projection = await coordinator.resolve_approval(
            step_id=steps[0].id,
            approval_id="approval-edge-1",
            subject_type="step",
            subject_id=steps[0].id,
            action="repository:write",
            outcome=cast(ApprovalOutcome, outcome_raw),
            resolution_key=f"approval-resolution-{outcome_raw}",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert projection.steps[0].status is expected_step
        assert projection.steps[0].phase is CoordinationPhase.TERMINAL
        assert kernel.task.status is expected_task

    asyncio.run(scenario())


def test_waiting_step_cancelled_before_deadline_never_wakes() -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime()
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        await coordinator.register_plan(plan, steps)
        await coordinator.wait_step(
            StepWait(
                wait_key="cancel-before-deadline",
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
        await coordinator.cancel_plan(plan.id, idempotency_key="cancel-wait", now=t0)
        await coordinator.process_due(now=t0 + timedelta(hours=1))
        projection = coordinator.projection(plan.id)
        assert projection.steps[0].status is StepStatus.CANCELLED
        assert projection.steps[0].phase is CoordinationPhase.TERMINAL
        assert projection.steps[0].wait_type is None
        assert kernel.task.status is TaskStatus.CANCELLED
        assert kernel.create_calls == 1

    asyncio.run(scenario())


def test_deadline_missed_during_downtime_resumes_once() -> None:
    async def scenario() -> None:
        plan, steps, repository, kernel, coordinator = _runtime()
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        await coordinator.register_plan(plan, steps)
        await coordinator.wait_step(
            StepWait(
                wait_key="missed-deadline",
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
            repository=repository,
            kernel=kernel,
            coordinator_id="restarted-edge-coordinator",
        )
        await restarted.process_due(now=t0 + timedelta(hours=1))
        assert restarted.projection(plan.id).steps[0].status is StepStatus.RUNNING
        await restarted.process_due(now=t0 + timedelta(hours=2))
        assert kernel.create_calls == 1

    asyncio.run(scenario())


def test_non_retryable_failure_does_not_schedule_retry() -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime()
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={
                steps[0].id: StepRetryPolicy(
                    max_attempts=3,
                    initial_delay_seconds=1,
                    retryable_categories=("transient",),
                )
            },
        )
        run_id = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_id, RunStatus.FAILED)
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            failure_category="permanent",
        )
        assert projection.steps[0].status is StepStatus.FAILED
        assert projection.steps[0].phase is CoordinationPhase.TERMINAL
        assert projection.steps[0].retry_due_at is None
        assert kernel.create_calls == 1
        assert kernel.task.status is TaskStatus.FAILED

    asyncio.run(scenario())


def test_retry_max_attempt_exhaustion_never_creates_third_run() -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime()
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={
                steps[0].id: StepRetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=1,
                    retryable_categories=("transient",),
                )
            },
        )
        first_run = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(first_run, RunStatus.FAILED)
        await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=first_run,
            failure_category="transient",
            now=t0,
        )
        await coordinator.process_due(now=t0 + timedelta(seconds=1))
        second = coordinator.projection(plan.id).steps[0]
        second_run = cast(str, second.latest_run_id)
        kernel.finish(second_run, RunStatus.FAILED)
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=second_run,
            failure_category="transient",
            now=t0 + timedelta(seconds=2),
        )
        await coordinator.process_due(now=t0 + timedelta(hours=1))
        assert projection.steps[0].status is StepStatus.FAILED
        assert projection.steps[0].phase is CoordinationPhase.TERMINAL
        assert projection.steps[0].current_attempt == 2
        assert kernel.create_calls == 2
        assert kernel.task.status is TaskStatus.FAILED

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("policy", "predecessor_run_status", "expected_dependent", "expected_task"),
    [
        (
            PredecessorFailurePolicy.FAIL_FAST,
            RunStatus.FAILED,
            StepStatus.SKIPPED,
            TaskStatus.FAILED,
        ),
        (
            PredecessorFailurePolicy.CANCEL_DEPENDENT,
            RunStatus.FAILED,
            StepStatus.CANCELLED,
            TaskStatus.FAILED,
        ),
        (
            PredecessorFailurePolicy.FAIL_FAST,
            RunStatus.CANCELLED,
            StepStatus.SKIPPED,
            TaskStatus.CANCELLED,
        ),
        (
            PredecessorFailurePolicy.CANCEL_DEPENDENT,
            RunStatus.CANCELLED,
            StepStatus.CANCELLED,
            TaskStatus.CANCELLED,
        ),
    ],
)
def test_failed_or_cancelled_predecessor_uses_explicit_policy(
    policy: PredecessorFailurePolicy,
    predecessor_run_status: RunStatus,
    expected_dependent: StepStatus,
    expected_task: TaskStatus,
) -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime(((), (0,)))
        projection = await coordinator.register_plan(
            plan,
            steps,
            predecessor_failure_policy=policy,
        )
        run_id = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_id, predecessor_run_status)
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            failure_category="permanent",
        )
        by_id = {item.step_id: item for item in projection.steps}
        assert by_id[steps[1].id].status is expected_dependent
        assert by_id[steps[1].id].phase is CoordinationPhase.TERMINAL
        assert kernel.create_calls == 1
        assert kernel.task.status is expected_task

    asyncio.run(scenario())


def test_late_success_after_cancellation_cannot_revive_step_or_task() -> None:
    async def scenario() -> None:
        plan, steps, _, kernel, coordinator = _runtime()
        projection = await coordinator.register_plan(plan, steps)
        run_id = cast(str, projection.steps[0].latest_run_id)
        await coordinator.cancel_plan(plan.id, idempotency_key="cancel-before-late-result")
        kernel.finish(run_id, RunStatus.SUCCEEDED)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_id)
        assert projection.steps[0].status is StepStatus.CANCELLED
        assert projection.steps[0].phase is CoordinationPhase.INCONSISTENT
        assert kernel.task.status is TaskStatus.CANCELLED
        assert kernel.create_calls == 1

    asyncio.run(scenario())


def test_two_coordinators_cannot_advance_same_ready_step_concurrently() -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((),))
        step = replace(steps[0], status=StepStatus.READY)
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(
            plan,
            (step,),
            (
                StepCoordinationRecord(
                    task_id=plan.task_id,
                    plan_id=plan.id,
                    plan_revision=plan.revision,
                    step_id=step.id,
                    phase=CoordinationPhase.READY,
                ),
            ),
        )
        kernel = EdgeKernel(plan, (step,))
        kernel.block_create = True
        first = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="coordinator-a",
        )
        second = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="coordinator-b",
        )

        first_advance = asyncio.create_task(first.advance(plan.id))
        await kernel.create_entered.wait()
        second_projection = await second.advance(plan.id)
        assert second_projection.steps[0].status is StepStatus.READY
        assert kernel.create_calls == 0
        kernel.release_create.set()
        await first_advance
        assert kernel.create_calls == 1
        await second.advance(plan.id)
        assert kernel.create_calls == 1

    asyncio.run(scenario())


def test_expired_claim_after_crash_allows_safe_takeover() -> None:
    async def scenario() -> None:
        plan, steps = _plan_steps(((),))
        step = replace(steps[0], status=StepStatus.READY)
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(
            plan,
            (step,),
            (
                StepCoordinationRecord(
                    task_id=plan.task_id,
                    plan_id=plan.id,
                    plan_revision=plan.revision,
                    step_id=step.id,
                    phase=CoordinationPhase.READY,
                ),
            ),
        )
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        crashed_claim = repository.acquire_claim(
            step_id=step.id,
            owner_id="crashed-coordinator",
            ttl=timedelta(seconds=5),
            now=t0,
        )
        assert crashed_claim is not None
        kernel = EdgeKernel(plan, (step,))
        restarted = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="replacement-coordinator",
        )
        projection = await restarted.advance(plan.id, now=t0 + timedelta(seconds=6))
        assert projection.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 1

    asyncio.run(scenario())
