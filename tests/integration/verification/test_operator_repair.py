from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    CoordinatorRepairAction,
    CoordinatorRepairService,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    ReconciliationDisposition,
    StepCoordinationRecord,
)
from ai_multi_agent_platform.domain import Plan, Step, StepStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


async def _canonical_plan(kernel: PlatformKernel) -> tuple[Plan, str]:
    project_id = new_id("project")
    created = await kernel.create_task(
        idempotency_key="repair:create",
        title="repair fixture",
        objective="repair only explicit inconsistent state",
        owner_type="user",
        owner_id="issue-384-repair-user",
        project_id=project_id,
    )
    await kernel.ready_task(idempotency_key="repair:ready", task_id=created.task_id)
    planned = await kernel.plan_task(idempotency_key="repair:plan", task_id=created.task_id)
    assert planned.plan_ref is not None
    assert len(planned.step_ids) == 1
    plan = Plan(
        id=planned.plan_ref,
        task_id=planned.task_id,
        owner_ref=planned.task.owner_ref,
        active=True,
        project_id=planned.task.project_id,
    )
    return plan, planned.step_ids[0]


def test_operator_can_cancel_only_an_explicit_missing_run_inconsistency_idempotently() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        plan, step_id = await _canonical_plan(kernel)
        step = Step(
            id=step_id,
            plan_id=plan.id,
            title="ambiguous running step",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
            status=StepStatus.RUNNING,
        )
        missing_run_id = new_id("run")
        record = StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=step.id,
            phase=CoordinationPhase.INCONSISTENT,
            latest_run_id=missing_run_id,
            current_attempt=1,
            reconciliation=ReconciliationDisposition.MISSING_CANONICAL_RUN,
            reconciliation_detail="referenced canonical Run is missing",
        )
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(plan, (step,), (record,))
        coordinator = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,
            coordinator_id="issue-384-repair-coordinator",
        )
        repairs = CoordinatorRepairService(coordinator)
        now = datetime(2026, 9, 6, 13, 0, tzinfo=UTC)

        repaired = await repairs.repair_step(
            plan_id=plan.id,
            step_id=step.id,
            action=CoordinatorRepairAction.CANCEL_MISSING_RUN,
            expected_revision=record.revision,
            idempotency_key="operator-repair-missing-run",
            now=now,
        )
        projection = repaired.steps[0]
        assert projection.step_id == step.id
        assert projection.latest_run_id == missing_run_id
        assert projection.status is StepStatus.CANCELLED
        assert projection.phase is CoordinationPhase.TERMINAL
        assert projection.reconciliation is ReconciliationDisposition.CANONICAL_TERMINAL
        assert (await kernel.get_task(plan.task_id)).status is TaskStatus.CANCELLED

        persisted = repository.get_step_record(step.id)
        assert persisted.processed_keys == ("repair:operator-repair-missing-run",)
        assert persisted.reconciliation_detail == "operator repair applied: cancel_missing_run"
        repaired_revision = persisted.revision

        replayed = await repairs.repair_step(
            plan_id=plan.id,
            step_id=step.id,
            action=CoordinatorRepairAction.CANCEL_MISSING_RUN,
            expected_revision=record.revision,
            idempotency_key="operator-repair-missing-run",
            now=now,
        )
        assert replayed == repaired
        assert repository.get_step_record(step.id).revision == repaired_revision
        assert repository.get_step_record(step.id).processed_keys == (
            "repair:operator-repair-missing-run",
        )

    asyncio.run(scenario())


def test_operator_repair_refuses_to_cancel_when_the_canonical_run_exists() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        plan, step_id = await _canonical_plan(kernel)
        run = await kernel.create_run(
            idempotency_key="repair:existing-run:create",
            task_id=plan.task_id,
            subject_type="step",
            subject_id=step_id,
        )
        step = Step(
            id=step_id,
            plan_id=plan.id,
            title="incorrectly marked missing",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
            status=StepStatus.RUNNING,
        )
        record = StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=step.id,
            phase=CoordinationPhase.INCONSISTENT,
            latest_run_id=run.run_id,
            current_attempt=1,
            reconciliation=ReconciliationDisposition.MISSING_CANONICAL_RUN,
        )
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(plan, (step,), (record,))
        repairs = CoordinatorRepairService(
            DurablePlanStepCoordinator(
                repository=repository,
                kernel=kernel,
                coordinator_id="issue-384-repair-coordinator",
            )
        )

        with pytest.raises(ContractError) as caught:
            await repairs.repair_step(
                plan_id=plan.id,
                step_id=step.id,
                action=CoordinatorRepairAction.CANCEL_MISSING_RUN,
                expected_revision=record.revision,
                idempotency_key="operator-repair-must-not-hide-run",
            )
        assert caught.value.code is ErrorCode.CONFLICT
        assert "Run now exists" in caught.value.message
        assert repository.get_step_record(step.id) == record

    asyncio.run(scenario())


def test_operator_can_acknowledge_terminal_canonical_state_without_rewriting_it() -> None:
    async def scenario() -> None:
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=InMemoryKernelRepository(),
        )
        plan, step_id = await _canonical_plan(kernel)
        await kernel.cancel_task(
            idempotency_key="repair:canonical-task-cancel",
            task_id=plan.task_id,
        )
        step = Step(
            id=step_id,
            plan_id=plan.id,
            title="already terminal canonical step",
            owner_ref=plan.owner_ref,
            project_id=plan.project_id,
            status=StepStatus.CANCELLED,
        )
        record = StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=step.id,
            phase=CoordinationPhase.INCONSISTENT,
            reconciliation=ReconciliationDisposition.INCONSISTENT,
            reconciliation_detail="terminal Step needs operator acknowledgement",
        )
        repository = InMemoryCoordinatorRepository()
        repository.create_plan(plan, (step,), (record,))
        repairs = CoordinatorRepairService(
            DurablePlanStepCoordinator(
                repository=repository,
                kernel=kernel,
                coordinator_id="issue-384-repair-coordinator",
            )
        )

        repaired = await repairs.repair_step(
            plan_id=plan.id,
            step_id=step.id,
            action=CoordinatorRepairAction.ACKNOWLEDGE_CANONICAL_TERMINAL,
            expected_revision=record.revision,
            idempotency_key="operator-ack-terminal",
        )
        assert repaired.steps[0].status is StepStatus.CANCELLED
        assert repaired.steps[0].phase is CoordinationPhase.TERMINAL
        assert repaired.steps[0].reconciliation is ReconciliationDisposition.CANONICAL_TERMINAL
        assert (await kernel.get_task(plan.task_id)).status is TaskStatus.CANCELLED

    asyncio.run(scenario())
