from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.contracts import ExecutionStatus, PlanRequest, PlanResponse
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
    StepWait,
    WaitType,
)
from ai_multi_agent_platform.domain import Plan, RunStatus, Step, StepStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class DisabledOrchestrator(FakeOrchestrator):
    """Replacement orchestrator that must never be consulted for an instantiated durable Plan."""

    async def plan(self, request: PlanRequest) -> PlanResponse:
        del request
        raise AssertionError("durable coordinator attempted to re-enter the disabled orchestrator")


async def _canonical_single_step(kernel: PlatformKernel) -> tuple[Plan, Step]:
    project_id = new_id("project")
    created = await kernel.create_task(
        idempotency_key="orchestrator-replacement:create",
        title="orchestrator replacement",
        objective="preserve the instantiated Plan and Step identity",
        owner_type="user",
        owner_id="issue-384-orchestrator-user",
        project_id=project_id,
    )
    await kernel.ready_task(
        idempotency_key="orchestrator-replacement:ready",
        task_id=created.task_id,
    )
    planned = await kernel.plan_task(
        idempotency_key="orchestrator-replacement:plan",
        task_id=created.task_id,
    )
    assert planned.plan_ref is not None
    assert len(planned.step_ids) == 1
    plan = Plan(
        id=planned.plan_ref,
        task_id=planned.task_id,
        owner_ref=planned.task.owner_ref,
        active=True,
        project_id=planned.task.project_id,
    )
    step = Step(
        id=planned.step_ids[0],
        plan_id=plan.id,
        title="durable instantiated step",
        owner_ref=planned.task.owner_ref,
        project_id=planned.task.project_id,
    )
    return plan, step


def test_orchestrator_replacement_does_not_change_durable_step_identity_or_state(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        kernel_path = tmp_path / "kernel.sqlite3"
        coordination_path = tmp_path / "coordination.sqlite3"
        lifecycle = FakeLifecycleBackend()
        original_orchestrator = FakeOrchestrator(summary_prefix="Hermes-compatible proposal")
        original_kernel = PlatformKernel(
            orchestrator=original_orchestrator,
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(kernel_path),
        )
        plan, step = await _canonical_single_step(original_kernel)
        original = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(coordination_path),
            kernel=original_kernel,
            coordinator_id="coordinator-before-orchestrator-replacement",
        )
        projection = await original.register_plan(plan, (step,))
        run_id = projection.steps[0].latest_run_id
        assert run_id is not None
        t0 = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
        wait = StepWait(
            wait_key="orchestrator-independent-deadline",
            wait_type=WaitType.DEADLINE,
            task_id=plan.task_id,
            plan_id=plan.id,
            step_id=step.id,
            owner_ref=step.owner_ref,
            project_id=step.project_id,
            deadline_at=t0 + timedelta(seconds=5),
            created_at=t0,
        )
        before = await original.wait_step(wait, now=t0)
        assert before.steps[0].status is StepStatus.WAITING
        assert before.steps[0].phase is CoordinationPhase.WAITING
        history_before = await original_kernel.history(plan.task_id)
        assert sum(event.event_type == "plan.created" for event in history_before) == 1
        assert len(original_orchestrator.calls) == 1

        # Replace/deactivate the orchestration adapter while retaining only platform-owned durable
        # kernel + coordinator stores. Runtime progression must not call the new orchestrator.
        replacement_kernel = PlatformKernel(
            orchestrator=DisabledOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(kernel_path),
        )
        restarted = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(coordination_path),
            kernel=replacement_kernel,
            coordinator_id="coordinator-after-orchestrator-replacement",
        )

        restored = restarted.projection(plan.id)
        assert restored == before
        assert restored.plan_id == plan.id
        assert restored.steps[0].step_id == step.id
        assert restored.steps[0].latest_run_id == run_id

        await restarted.process_due(now=t0 + timedelta(seconds=5))
        resumed = restarted.projection(plan.id).steps[0]
        assert resumed.step_id == step.id
        assert resumed.latest_run_id == run_id
        assert resumed.status is StepStatus.RUNNING
        assert resumed.phase is CoordinationPhase.ATTEMPT_ACTIVE
        assert (await replacement_kernel.get_run(plan.task_id, run_id)).status is RunStatus.RUNNING

        lifecycle.complete(run_id, status=ExecutionStatus.SUCCEEDED)
        run = await replacement_kernel.refresh_run(
            idempotency_key="orchestrator-replacement:refresh-success",
            task_id=plan.task_id,
            run_id=run_id,
        )
        assert run.status is RunStatus.SUCCEEDED
        final = await restarted.observe_run(task_id=plan.task_id, run_id=run_id)
        assert final.plan_id == plan.id
        assert final.steps[0].step_id == step.id
        assert final.steps[0].latest_run_id == run_id
        assert final.steps[0].status is StepStatus.SUCCEEDED
        assert (await replacement_kernel.get_task(plan.task_id)).status is TaskStatus.SUCCEEDED

        history_after = await replacement_kernel.history(plan.task_id)
        assert sum(event.event_type == "plan.created" for event in history_after) == 1
        assert history_after[: len(history_before)] == history_before

    asyncio.run(scenario())
