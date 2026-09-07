from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    CoordinatorPlanResourceService,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    RetryState,
    SQLiteCoordinatorRepository,
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


class WorkflowProgressKernel:
    """Small canonical Run kernel for issue #560 projection acceptance tests."""

    def __init__(self, plan: Plan, steps: tuple[Step, ...]) -> None:
        self.task = TaskState(
            task=Task(
                id=plan.task_id,
                title="Issue 560 workflow progress",
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
        assert subject_type == "step" and subject_id is not None
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

    def finish(self, run_id: str, status: RunStatus) -> RunState:
        current = self.runs[run_id]
        terminal = replace(
            current,
            run=replace(current.run, status=status),
            revision=current.revision + 1,
        )
        self.runs[run_id] = terminal
        return terminal


def _plan_and_steps(count: int = 1) -> tuple[Plan, tuple[Step, ...]]:
    owner = OwnerRef(type="user", id="issue-560-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    steps = tuple(
        Step(
            plan_id=plan.id,
            title=f"step-{index}",
            owner_ref=owner,
            project_id=plan.project_id,
        )
        for index in range(count)
    )
    return plan, steps


def _coordinator(
    plan: Plan,
    steps: tuple[Step, ...],
    *,
    repository: InMemoryCoordinatorRepository | SQLiteCoordinatorRepository | None = None,
    kernel: WorkflowProgressKernel | None = None,
) -> tuple[DurablePlanStepCoordinator, WorkflowProgressKernel]:
    runtime_kernel = kernel or WorkflowProgressKernel(plan, steps)
    coordinator = DurablePlanStepCoordinator(
        repository=repository or InMemoryCoordinatorRepository(),
        kernel=runtime_kernel,
        coordinator_id="issue-560-coordinator",
    )
    return coordinator, runtime_kernel


def _resource(coordinator: DurablePlanStepCoordinator, plan_id: str) -> dict[str, object]:
    resource = asyncio.run(
        CoordinatorPlanResourceService(coordinator).get_resource(
            RequestContext(request_id="req-560", correlation_id="corr-560"),
            plan_id,
        )
    )
    return cast(dict[str, object], resource)


def test_control_plane_projects_only_safe_wait_context_and_resolution() -> None:
    async def scenario() -> tuple[DurablePlanStepCoordinator, Plan, tuple[Step, ...]]:
        plan, steps = _plan_and_steps(3)
        coordinator, _ = _coordinator(plan, steps)
        await coordinator.register_plan(plan, steps)
        deadline = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)
        await coordinator.wait_step(
            StepWait(
                wait_key="approval-wait-560",
                wait_type=WaitType.APPROVAL,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[0].id,
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
                deadline_at=deadline,
                approval_id="approval_560",
                approval_subject_type="step",
                approval_subject_id=steps[0].id,
                approval_action="execute",
            )
        )
        await coordinator.wait_step(
            StepWait(
                wait_key="event-wait-560",
                wait_type=WaitType.EVENT,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[1].id,
                owner_ref=steps[1].owner_ref,
                project_id=steps[1].project_id,
                event_type="connector.completed",
                correlation_key="event-correlation-560",
            )
        )
        await coordinator.wait_step(
            StepWait(
                wait_key="job-wait-560",
                wait_type=WaitType.EXTERNAL_JOB,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[2].id,
                owner_ref=steps[2].owner_ref,
                project_id=steps[2].project_id,
                external_job_ref="adapter-job-560",
            )
        )
        await coordinator.resolve_approval(
            step_id=steps[0].id,
            approval_id="approval_560",
            subject_type="step",
            subject_id=steps[0].id,
            action="execute",
            outcome="expired",
            resolution_key="approval-expired-560",
            owner_ref=steps[0].owner_ref,
            project_id=steps[0].project_id,
            now=deadline + timedelta(seconds=1),
        )
        return coordinator, plan, steps

    coordinator, plan, steps = asyncio.run(scenario())
    resource = _resource(coordinator, plan.id)
    raw_steps = cast(list[dict[str, object]], resource["steps"])
    by_id = {cast(str, item["id"]): item for item in raw_steps}

    approval = by_id[steps[0].id]
    assert approval["wait_state"] == "expired"
    assert approval["wait_key"] == "approval-wait-560"
    assert approval["wait_approval_id"] == "approval_560"
    assert approval["wait_approval_subject_type"] == "step"
    assert approval["wait_approval_subject_id"] == steps[0].id
    assert approval["wait_approval_action"] == "execute"
    assert approval["wait_resolved_at"] is not None

    event = by_id[steps[1].id]
    assert event["wait_state"] == "active"
    assert event["wait_event_type"] == "connector.completed"
    assert event["wait_correlation_key"] == "event-correlation-560"

    external = by_id[steps[2].id]
    assert external["wait_state"] == "active"
    assert external["wait_external_job_ref"] == "adapter-job-560"

    serialized = json.dumps(resource, sort_keys=True)
    for forbidden in (
        "processed_keys",
        "retry_policy",
        "resolution_key",
        "provenance_source",
        "claim_id",
        "lease_token",
        "backend_workflow_id",
        "raw_provider_payload",
    ):
        assert forbidden not in serialized


def test_foreign_scope_signal_cannot_resolve_or_reveal_hidden_event_wait_context() -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps()
        coordinator, _ = _coordinator(plan, steps)
        await coordinator.register_plan(plan, steps)
        await coordinator.wait_step(
            StepWait(
                wait_key="scoped-event-560",
                wait_type=WaitType.EVENT,
                task_id=plan.task_id,
                plan_id=plan.id,
                step_id=steps[0].id,
                owner_ref=steps[0].owner_ref,
                project_id=steps[0].project_id,
                event_type="secret-free.event",
                correlation_key="safe-correlation-560",
            )
        )
        with pytest.raises(ContractError) as exc_info:
            await coordinator.resolve_event(
                step_id=steps[0].id,
                event_id="foreign-event-560",
                event_type="secret-free.event",
                correlation_key="safe-correlation-560",
                owner_ref=OwnerRef(type="user", id="foreign-user"),
                project_id=steps[0].project_id,
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        record = coordinator.repository.get_step_record(steps[0].id)
        assert record.phase is CoordinationPhase.WAITING
        assert record.wait is not None and record.wait.resolution is None

    asyncio.run(scenario())


def test_retry_state_is_explicit_for_scheduled_active_exhausted_and_non_retryable() -> None:
    async def exhausted_scenario() -> None:
        plan, steps = _plan_and_steps()
        coordinator, kernel = _coordinator(plan, steps)
        policy = StepRetryPolicy(
            max_attempts=2,
            initial_delay_seconds=10,
            retryable_categories=("transient",),
        )
        t0 = datetime(2026, 9, 8, 10, 0, tzinfo=UTC)
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={steps[0].id: policy},
        )
        first_run = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(first_run, RunStatus.FAILED)
        await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=first_run,
            failure_category="transient",
            now=t0,
        )
        scheduled = coordinator.repository.get_step_record(steps[0].id)
        assert scheduled.retry_state is RetryState.SCHEDULED
        assert scheduled.retry_due_at == t0 + timedelta(seconds=10)
        assert _resource(coordinator, plan.id)["steps"][0]["retry_state"] == "scheduled"  # type: ignore[index]

        await coordinator.process_due(now=t0 + timedelta(seconds=10))
        active = coordinator.repository.get_step_record(steps[0].id)
        assert active.retry_state is RetryState.ACTIVE
        assert active.current_attempt == 2
        assert active.retry_due_at is None

        second_run = cast(str, coordinator.projection(plan.id).steps[0].latest_run_id)
        kernel.finish(second_run, RunStatus.FAILED)
        await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=second_run,
            failure_category="transient",
            now=t0 + timedelta(seconds=11),
        )
        exhausted = coordinator.repository.get_step_record(steps[0].id)
        assert exhausted.retry_state is RetryState.EXHAUSTED
        assert exhausted.current_attempt == 2
        resource = _resource(coordinator, plan.id)
        projected = cast(list[dict[str, object]], resource["steps"])[0]
        assert projected["retry_state"] == "exhausted"
        assert projected["retry_max_attempts"] == 2
        assert projected["retry_due_at"] is None

    async def not_retryable_scenario() -> None:
        plan, steps = _plan_and_steps()
        coordinator, kernel = _coordinator(plan, steps)
        policy = StepRetryPolicy(
            max_attempts=3,
            retryable_categories=("transient",),
        )
        projection = await coordinator.register_plan(
            plan,
            steps,
            retry_policies={steps[0].id: policy},
        )
        run_id = cast(str, projection.steps[0].latest_run_id)
        kernel.finish(run_id, RunStatus.FAILED)
        await coordinator.observe_run(
            task_id=plan.task_id,
            run_id=run_id,
            failure_category="permanent",
        )
        record = coordinator.repository.get_step_record(steps[0].id)
        assert record.current_attempt == 1
        assert record.retry_state is RetryState.NOT_RETRYABLE
        resource = _resource(coordinator, plan.id)
        projected = cast(list[dict[str, object]], resource["steps"])[0]
        assert projected["retry_state"] == "not_retryable"
        assert projected["retry_max_attempts"] == 3

    asyncio.run(exhausted_scenario())
    asyncio.run(not_retryable_scenario())


def test_retry_state_survives_sqlite_restart_without_client_inference(tmp_path: Path) -> None:
    async def scenario() -> None:
        plan, steps = _plan_and_steps()
        database = tmp_path / "coordination.sqlite3"
        kernel = WorkflowProgressKernel(plan, steps)
        first = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(database),
            kernel=kernel,
            coordinator_id="issue-560-before-restart",
        )
        policy = StepRetryPolicy(
            max_attempts=2,
            initial_delay_seconds=5,
            retryable_categories=("transient",),
        )
        t0 = datetime(2026, 9, 8, 11, 0, tzinfo=UTC)
        projection = await first.register_plan(
            plan,
            steps,
            retry_policies={steps[0].id: policy},
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
            repository=SQLiteCoordinatorRepository(database),
            kernel=kernel,
            coordinator_id="issue-560-after-restart",
        )
        restored = restarted.repository.get_step_record(steps[0].id)
        assert restored.retry_state is RetryState.SCHEDULED
        assert restored.retry_due_at == t0 + timedelta(seconds=5)
        resource = await CoordinatorPlanResourceService(restarted).get_resource(
            RequestContext(request_id="req-restart-560", correlation_id="corr-restart-560"),
            plan.id,
        )
        projected = cast(list[dict[str, object]], resource["steps"])[0]
        assert projected["retry_state"] == "scheduled"
        assert projected["retry_due_at"] == (t0 + timedelta(seconds=5)).isoformat()

    asyncio.run(scenario())
