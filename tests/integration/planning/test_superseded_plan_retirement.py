from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.coordination import (
    COORDINATOR_MIGRATION_REVISION,
    COORDINATOR_SCHEMA_VERSION,
    CoordinationPhase,
    DurablePlanStepCoordinator,
    InMemoryCoordinatorRepository,
    RetryState,
    SQLiteCoordinatorRepository,
    StepCoordinationRecord,
    StepRetryPolicy,
    inspect_coordinator_store,
    migrate_coordinator_store,
)
from ai_multi_agent_platform.coordination.sqlite_repository_v2 import (
    SQLiteCoordinatorRepository as V2SQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.domain import (
    OwnerRef,
    Plan,
    Step,
    StepStatus,
    Task,
    TaskStatus,
    new_id,
)
from ai_multi_agent_platform.kernel.models import TaskState

OWNER = OwnerRef(type="user", id="issue-439-plan-retirement-user")


class RetirementKernel:
    def __init__(self, task: TaskState) -> None:
        self.task = task
        self.get_task_calls = 0

    async def get_task(self, task_id: str) -> TaskState:
        assert task_id == self.task.task_id
        self.get_task_calls += 1
        return self.task


def _superseded_state() -> tuple[
    InMemoryCoordinatorRepository,
    RetirementKernel,
    Plan,
    Plan,
    Step,
    Step,
    Step,
]:
    task_id = new_id("task")
    old_plan = Plan(task_id=task_id, owner_ref=OWNER, active=True, revision=1)
    replacement_plan = Plan(task_id=task_id, owner_ref=OWNER, active=True, revision=2)
    completed = Step(
        plan_id=old_plan.id,
        title="completed reusable work",
        owner_ref=OWNER,
        status=StepStatus.SUCCEEDED,
    )
    not_started = Step(
        plan_id=old_plan.id,
        title="not-started work",
        owner_ref=OWNER,
        depends_on=(completed.id,),
    )
    retry = Step(
        plan_id=old_plan.id,
        title="failed work awaiting retry",
        owner_ref=OWNER,
        status=StepStatus.FAILED,
    )
    now = datetime(2026, 9, 7, 22, 0, tzinfo=UTC)
    records = (
        StepCoordinationRecord(
            task_id=task_id,
            plan_id=old_plan.id,
            plan_revision=old_plan.revision,
            step_id=completed.id,
            phase=CoordinationPhase.TERMINAL,
        ),
        StepCoordinationRecord(
            task_id=task_id,
            plan_id=old_plan.id,
            plan_revision=old_plan.revision,
            step_id=not_started.id,
            phase=CoordinationPhase.BLOCKED,
            dependency_ids=(completed.id,),
            satisfied_dependency_ids=(completed.id,),
        ),
        StepCoordinationRecord(
            task_id=task_id,
            plan_id=old_plan.id,
            plan_revision=old_plan.revision,
            step_id=retry.id,
            phase=CoordinationPhase.RETRY_SCHEDULED,
            current_attempt=1,
            retry_policy=StepRetryPolicy(
                max_attempts=2,
                retryable_categories=("transient",),
            ),
            retry_due_at=now + timedelta(minutes=5),
            retry_state=RetryState.SCHEDULED,
        ),
    )
    repository = InMemoryCoordinatorRepository()
    repository.create_plan(old_plan, (completed, not_started, retry), records)
    kernel = RetirementKernel(
        TaskState(
            task=Task(
                id=task_id,
                title="superseded-plan retirement",
                owner_ref=OWNER,
                status=TaskStatus.FAILED,
            ),
            revision=7,
            plan_ref=replacement_plan.id,
        )
    )
    return (
        repository,
        kernel,
        old_plan,
        replacement_plan,
        completed,
        not_started,
        retry,
    )


def test_reconciliation_retires_superseded_not_started_work_without_rewriting_completed_work() -> (
    None
):
    async def scenario() -> None:
        (
            repository,
            kernel,
            old_plan,
            replacement_plan,
            completed,
            not_started,
            retry,
        ) = _superseded_state()
        coordinator = DurablePlanStepCoordinator(
            repository=repository,
            kernel=kernel,  # type: ignore[arg-type]
            coordinator_id="issue-439-retirement",
        )

        projections = await coordinator.reconcile_all(now=datetime(2026, 9, 7, 22, 1, tzinfo=UTC))
        assert len(projections) == 1
        retirement = repository.plan_retirement(old_plan.id)
        assert retirement is not None
        assert retirement.superseded_by_plan_id == replacement_plan.id
        assert retirement.reason == "canonical_plan_superseded"
        assert repository.list_active_plans() == ()

        retired = repository.get_plan(old_plan.id)
        assert retired.step(completed.id).status is StepStatus.SUCCEEDED
        assert retired.step(not_started.id).status is StepStatus.CANCELLED
        assert retired.step(retry.id).status is StepStatus.FAILED
        not_started_record = repository.get_step_record(not_started.id)
        retry_record = repository.get_step_record(retry.id)
        assert not_started_record.phase is CoordinationPhase.TERMINAL
        assert retry_record.phase is CoordinationPhase.TERMINAL
        assert retry_record.retry_due_at is None
        assert retry_record.retry_state is RetryState.CANCELLED

        # Retirement is idempotent and removes the old Plan from future global reconciliation.
        calls_after_retirement = kernel.get_task_calls
        assert await coordinator.reconcile_all() == ()
        assert kernel.get_task_calls == calls_after_retirement

    asyncio.run(scenario())


def _single_pending_plan() -> tuple[Plan, Step, StepCoordinationRecord]:
    plan = Plan(task_id=new_id("task"), owner_ref=OWNER, active=True)
    step = Step(plan_id=plan.id, title="pending", owner_ref=OWNER)
    record = StepCoordinationRecord(
        task_id=plan.task_id,
        plan_id=plan.id,
        plan_revision=plan.revision,
        step_id=step.id,
        phase=CoordinationPhase.BLOCKED,
    )
    return plan, step, record


def test_sqlite_plan_retirement_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "coordination.sqlite3"
    repository = SQLiteCoordinatorRepository(path)
    plan, step, record = _single_pending_plan()
    repository.create_plan(plan, (step,), (record,))
    replacement_plan_id = new_id("plan")
    retired_at = datetime(2026, 9, 7, 22, 5, tzinfo=UTC)

    retirement = repository.retire_plan(
        plan.id,
        superseded_by_plan_id=replacement_plan_id,
        reason="canonical_plan_superseded",
        retired_at=retired_at,
    )
    assert retirement.plan_id == plan.id
    assert repository.list_active_plans() == ()

    restarted = SQLiteCoordinatorRepository(path)
    persisted = restarted.plan_retirement(plan.id)
    assert persisted is not None
    assert persisted.superseded_by_plan_id == replacement_plan_id
    assert persisted.retired_at == retired_at
    assert restarted.get_plan(plan.id).plan == plan
    assert restarted.list_active_plans() == ()


def test_v2_store_requires_explicit_v3_migration_and_preserves_active_plan(tmp_path: Path) -> None:
    path = tmp_path / "coordination.sqlite3"
    v2 = V2SQLiteCoordinatorRepository(path)
    plan, step, record = _single_pending_plan()
    v2.create_plan(plan, (step,), (record,))

    before = inspect_coordinator_store(path)
    assert before is not None
    assert before.schema_version == 2
    with pytest.raises(RuntimeError, match="explicit platform upgrade"):
        SQLiteCoordinatorRepository(path)

    assert migrate_coordinator_store(path) == (COORDINATOR_MIGRATION_REVISION,)
    after = inspect_coordinator_store(path)
    assert after is not None
    assert after.schema_version == COORDINATOR_SCHEMA_VERSION
    assert after.migration_revision == COORDINATOR_MIGRATION_REVISION

    migrated = SQLiteCoordinatorRepository(path)
    assert migrated.get_plan(plan.id).plan == plan
    assert migrated.plan_retirement(plan.id) is None
    assert tuple(state.plan.id for state in migrated.list_active_plans()) == (plan.id,)
