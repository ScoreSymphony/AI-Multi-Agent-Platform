from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    ReconciliationDisposition,
    StepRetryPolicy,
    StepWait,
    WaitResolution,
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
from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry


class FakeKernel:
    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        task = Task(
            id=plan.task_id,
            title="coordination test",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
            status=TaskStatus.READY,
        )
        self.task = TaskState(
            task=task,
            revision=1,
            plan_ref=plan.id,
            step_ids=tuple(step.id for step in steps),
        )
        self.runs: dict[str, RunState] = {}
        self.by_key: dict[str, str] = {}
        self.start_keys: set[str] = set()
        self.cancel_keys: set[str] = set()
        self.create_calls = 0
        self.recover_calls = 0
        self.fail_start_once = False

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        return self.task

    async def get_run(self, task_id: str, run_id: str) -> RunState:
        assert task_id == self.task.task_id
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"run not found: {run_id}") from exc

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
        if subject_type != "step" or subject_id is None:
            raise AssertionError("coordinator must create only Step Runs")
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
        if self.fail_start_once:
            self.fail_start_once = False
            raise RuntimeError("simulated crash before dispatch")
        if idempotency_key in self.start_keys:
            return self.runs[run_id]
        self.start_keys.add(idempotency_key)
        current = await self.get_run(task_id, run_id)
        state = replace(
            current,
            run=replace(current.run, status=RunStatus.RUNNING),
            revision=current.revision + 1,
        )
        self.runs[run_id] = state
        if self.task.status is TaskStatus.READY:
            self.task = replace(
                self.task,
                task=replace(self.task.task, status=TaskStatus.RUNNING),
                revision=self.task.revision + 1,
            )
        return state

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
        state = replace(
            current,
            run=replace(current.run, status=RunStatus.CANCELLED),
            revision=current.revision + 1,
        )
        self.runs[run_id] = state
        return state

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
        self.recover_calls += 1
        return RecoveryReport(task_id=task_id, entries=())

    def finish(self, run_id: str, status: RunStatus) -> RunState:
        assert status in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.TIMED_OUT,
        }
        current = self.runs[run_id]
        state = replace(
            current,
            run=replace(current.run, status=status),
            revision=current.revision + 1,
        )
        self.runs[run_id] = state
        return state


def _plan_and_steps(
    dependencies: tuple[tuple[int, ...], ...],
) -> tuple[Plan, tuple[Step, ...]]:
    owner = OwnerRef(type="user", id="coordination-test-user")
    task_id = new_id("task")
    project_id = new_id("project")
    plan = Plan(
        task_id=task_id,
        owner_ref=owner,
        active=True,
        project_id=project_id,
    )
    ids = tuple(new_id("step") for _ in dependencies)
    steps = tuple(
        Step(
            id=ids[index],
            plan_id=plan.id,
            title=f"step-{index}",
            owner_ref=owner,
            project_id=project_id,
            depends_on=tuple(ids[pred] for pred in predecessors),
        )
        for index, predecessors in enumerate(dependencies)
    )
    return plan, steps


def _coordinator(
    plan: Plan,
    steps: tuple[Step, ...],
    *,
    repository: InMemoryCoordinatorRepository | None = None,
    kernel: FakeKernel | None = None,
) -> tuple[DurablePlanStepCoordinator, InMemoryCoordinatorRepository, FakeKernel, InMemoryExporter]:
    store = repository or InMemoryCoordinatorRepository()
    fake = kernel or FakeKernel(plan, steps)
    exporter = InMemoryExporter()
    coordinator = DurablePlanStepCoordinator(
        repository=store,
        kernel=fake,
        coordinator_id="coordinator-A",
        telemetry=Telemetry(exporter),
    )
    return coordinator, store, fake, exporter


def test_rejects_cycles_before_any_run_is_created() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((), (0,)))
        cyclic = (replace(steps[0], depends_on=(steps[1].id,)), steps[1])
        coordinator, _, kernel, _ = _coordinator(plan, cyclic)
        with pytest.raises(ContractError, match="cycle"):
            await coordinator.register_plan(plan, cyclic)
        assert kernel.create_calls == 0

    asyncio.run(scenario())


def test_diamond_fan_out_fan_in_and_duplicate_run_observation_are_exactly_once() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((), (0,), (0,), (1, 2)))
        coordinator, store, kernel, exporter = _coordinator(plan, steps)
        projection = await coordinator.register_plan(plan, steps)
        assert projection.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 1

        run_a = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_a, RunStatus.SUCCEEDED)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_a)
        by_id = {step.step_id: step for step in projection.steps}
        assert by_id[steps[1].id].status is StepStatus.RUNNING
        assert by_id[steps[2].id].status is StepStatus.RUNNING
        assert by_id[steps[3].id].status is StepStatus.PENDING
        assert kernel.create_calls == 3

        run_b = cast(str, by_id[steps[1].id].latest_run_id)
        kernel.finish(run_b, RunStatus.SUCCEEDED)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_b)
        assert kernel.create_calls == 3
        d = {step.step_id: step for step in projection.steps}[steps[3].id]
        assert d.satisfied_dependency_ids == (steps[1].id,)
        await coordinator.observe_run(task_id=plan.task_id, run_id=run_b)
        assert kernel.create_calls == 3

        run_c = cast(
            str,
            {step.step_id: step for step in projection.steps}[steps[2].id].latest_run_id,
        )
        kernel.finish(run_c, RunStatus.SUCCEEDED)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_c)
        d = {step.step_id: step for step in projection.steps}[steps[3].id]
        assert d.status is StepStatus.RUNNING
        assert set(d.satisfied_dependency_ids) == {steps[1].id, steps[2].id}
        assert kernel.create_calls == 4

        run_d = cast(str, d.latest_run_id)
        kernel.finish(run_d, RunStatus.SUCCEEDED)
        projection = await coordinator.observe_run(task_id=plan.task_id, run_id=run_d)
        assert all(item.status is StepStatus.SUCCEEDED for item in projection.steps)
        assert kernel.task.status is TaskStatus.SUCCEEDED
        assert any(item.event_name == "coordination.barrier.progress" for item in exporter.timeline)
        assert store.get_plan(plan.id).store_revision > 1

    asyncio.run(scenario())


def test_step_retry_uses_persisted_deadline_and_distinct_canonical_run_attempt() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, _, kernel, _ = _coordinator(plan, steps)
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        policy = StepRetryPolicy(
            max_attempts=2,
            initial_delay_seconds=10,
            multiplier=2,
            max_delay_seconds=60,
            retryable_categories=("transient",),
        )
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={steps[0].id: policy},
        )
        first_run = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(first_run, RunStatus.FAILED)
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=first_run,
            failure_category="transient",
            now=t0,
        )
        first = projection.steps[0]
        assert first.status is StepStatus.FAILED
        assert first.phase is CoordinationPhase.RETRY_SCHEDULED
        assert first.retry_due_at == t0 + timedelta(seconds=10)
        assert kernel.create_calls == 1

        await coordinator.process_due(now=t0 + timedelta(seconds=9))
        assert kernel.create_calls == 1
        await coordinator.process_due(now=t0 + timedelta(seconds=10))
        second = coordinator.projection(plan.id).steps[0]
        assert second.status is StepStatus.RUNNING
        assert second.current_attempt == 2
        assert second.latest_run_id != first_run
        assert kernel.create_calls == 2
        await coordinator.process_due(now=t0 + timedelta(seconds=11))
        assert kernel.create_calls == 2

        second_run = cast(str, second.latest_run_id)
        kernel.finish(second_run, RunStatus.SUCCEEDED)
        await coordinator.observe_run(task_id=plan.task_id, run_id=second_run)
        assert kernel.task.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_event_wait_rejects_foreign_scope_and_resumes_once() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, _, kernel, _ = _coordinator(plan, steps)
        projection = await coordinator.register_plan(plan, steps)
        run_id = cast(str, projection.steps[0].latest_run_id)
        wait = StepWait(
            wait_key="event-wait-1",
            wait_type=WaitType.EVENT,
            task_id=plan.task_id,
            plan_id=plan.id,
            step_id=steps[0].id,
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
            event_type="connector.completed",
            correlation_key="external-42",
        )
        projection = await coordinator.wait_step(wait)
        assert projection.steps[0].status is StepStatus.WAITING

        with pytest.raises(ContractError) as exc_info:
            await coordinator.resolve_event(
                step_id=steps[0].id,
                event_id="event-foreign",
                event_type="connector.completed",
                correlation_key="external-42",
                owner_ref=OwnerRef(type="user", id="foreign-user"),
                project_id=steps[0].project_id,
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN

        projection = await coordinator.resolve_event(
            step_id=steps[0].id,
            event_id="event-1",
            event_type="connector.completed",
            correlation_key="external-42",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert projection.steps[0].status is StepStatus.RUNNING
        duplicate = await coordinator.resolve_event(
            step_id=steps[0].id,
            event_id="event-1",
            event_type="connector.completed",
            correlation_key="external-42",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert duplicate.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 1

        kernel.finish(run_id, RunStatus.SUCCEEDED)
        await coordinator.observe_run(task_id=plan.task_id, run_id=run_id)
        assert kernel.task.status is TaskStatus.SUCCEEDED

    asyncio.run(scenario())


def test_approval_wait_is_bound_to_exact_subject_action_and_duplicate_decision() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, _, _, _ = _coordinator(plan, steps)
        await coordinator.register_plan(plan, steps)
        wait = StepWait(
            wait_key="approval-wait-1",
            wait_type=WaitType.APPROVAL,
            task_id=plan.task_id,
            plan_id=plan.id,
            step_id=steps[0].id,
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
            approval_id="approval-42",
            approval_subject_type="step",
            approval_subject_id=steps[0].id,
            approval_action="repository:write",
        )
        await coordinator.wait_step(wait)
        with pytest.raises(ContractError, match="subject/action"):
            await coordinator.resolve_approval(
                step_id=steps[0].id,
                approval_id="approval-42",
                subject_type="step",
                subject_id=steps[0].id,
                action="repository:merge",
                outcome="approved",
                resolution_key="approval-decision-1",
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
            )
        await coordinator.resolve_approval(
            step_id=steps[0].id,
            approval_id="approval-42",
            subject_type="step",
            subject_id=steps[0].id,
            action="repository:write",
            outcome="approved",
            resolution_key="approval-decision-1",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        duplicate = await coordinator.resolve_approval(
            step_id=steps[0].id,
            approval_id="approval-42",
            subject_type="step",
            subject_id=steps[0].id,
            action="repository:write",
            outcome="approved",
            resolution_key="approval-decision-1",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert duplicate.steps[0].status is StepStatus.RUNNING

    asyncio.run(scenario())


def test_external_job_wait_uses_exact_reference_and_scope() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, _, _, _ = _coordinator(plan, steps)
        await coordinator.register_plan(plan, steps)
        await coordinator.wait_step(
            StepWait(
                wait_key="job-wait-1",
                wait_type=WaitType.EXTERNAL_JOB,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[0].id,
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
                external_job_ref="adapter-job-42",
            )
        )
        with pytest.raises(ContractError, match="reference"):
            await coordinator.resolve_external_job(
                step_id=steps[0].id,
                external_job_ref="adapter-job-foreign",
                resolution=WaitResolution.SATISFIED,
                resolution_key="job-result-1",
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
            )
        projection = await coordinator.resolve_external_job(
            step_id=steps[0].id,
            external_job_ref="adapter-job-42",
            resolution=WaitResolution.SATISFIED,
            resolution_key="job-result-1",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert projection.steps[0].status is StepStatus.RUNNING
        duplicate = await coordinator.resolve_external_job(
            step_id=steps[0].id,
            external_job_ref="adapter-job-42",
            resolution=WaitResolution.SATISFIED,
            resolution_key="job-result-1",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
        )
        assert duplicate.steps[0].status is StepStatus.RUNNING

    asyncio.run(scenario())


def test_cancellation_suppresses_pending_retry_and_propagates_to_task() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, _, kernel, _ = _coordinator(plan, steps)
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
        policy = StepRetryPolicy(
            max_attempts=3,
            initial_delay_seconds=30,
            retryable_categories=("transient",),
        )
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={steps[0].id: policy},
        )
        run_id = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_id, RunStatus.FAILED)
        projection = await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            failure_category="transient",
            now=t0,
        )
        assert projection.steps[0].phase is CoordinationPhase.RETRY_SCHEDULED
        await coordinator.cancel_plan(plan.id, idempotency_key="cancel-1", now=t0)
        await coordinator.process_due(now=t0 + timedelta(hours=1))
        assert kernel.create_calls == 1
        assert kernel.task.status is TaskStatus.CANCELLED
        assert coordinator.projection(plan.id).steps[0].phase is CoordinationPhase.TERMINAL

    asyncio.run(scenario())


def test_reconciliation_never_blindly_recreates_missing_canonical_run() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, store, kernel, _ = _coordinator(plan, steps)
        projection = await coordinator.register_plan(plan, steps)
        run_id = cast(str, projection.steps[0].latest_run_id)
        del kernel.runs[run_id]
        before = kernel.create_calls
        projection = await coordinator.reconcile_plan(plan.id)
        assert kernel.recover_calls == 1
        assert kernel.create_calls == before
        assert projection.steps[0].phase is CoordinationPhase.INCONSISTENT
        assert (
            projection.steps[0].reconciliation
            is ReconciliationDisposition.MISSING_CANONICAL_RUN
        )
        assert store.get_step_record(steps[0].id).reconciliation_detail is not None

    asyncio.run(scenario())


def test_crash_after_coordinator_commit_before_start_is_recovered_idempotently() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps(((),))
        coordinator, store, kernel, _ = _coordinator(plan, steps)
        kernel.fail_start_once = True
        with pytest.raises(RuntimeError, match="simulated crash"):
            await coordinator.register_plan(plan, steps)
        persisted = store.get_step_record(steps[0].id)
        assert persisted.phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert persisted.latest_run_id is not None
        assert kernel.create_calls == 1

        restarted = DurablePlanStepCoordinator(
            repository=store,
            kernel=kernel,
            coordinator_id="coordinator-B",
        )
        projection = await restarted.reconcile_plan(plan.id)
        assert projection.steps[0].status is StepStatus.RUNNING
        assert kernel.create_calls == 1
        assert len(kernel.start_keys) == 1

    asyncio.run(scenario())
