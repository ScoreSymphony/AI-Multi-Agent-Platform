from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import create_single_node_backup, restore_single_node_backup
from ai_multi_agent_platform.backup.inventory import required_single_node_store_paths
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    DurablePlanStepCoordinator,
    SQLiteCoordinatorRepository,
    StepRetryPolicy,
    StepWait,
    WaitType,
)
from ai_multi_agent_platform.domain import (
    Event,
    Plan,
    RunStatus,
    Step,
    StepStatus,
    TaskStatus,
    new_id,
)
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _materialize_required_stores(root: Path) -> None:
    for relative in required_single_node_store_paths():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with sqlite3.connect(path):
                pass


async def _planned_single_step(
    kernel: PlatformKernel,
    *,
    key: str,
) -> tuple[Plan, Step]:
    project_id = new_id("project")
    created = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title=f"{key} task",
        objective=f"{key} objective",
        owner_type="user",
        owner_id="coordination-restore-user",
        project_id=project_id,
    )
    await kernel.ready_task(idempotency_key=f"{key}:ready", task_id=created.task_id)
    planned = await kernel.plan_task(idempotency_key=f"{key}:plan", task_id=created.task_id)
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
        title=f"{key} step",
        owner_ref=planned.task.owner_ref,
        project_id=planned.task.project_id,
    )
    return plan, step


def _event_ids(events: tuple[Event, ...]) -> tuple[str, ...]:
    return tuple(event.id for event in events)


def test_restore_preserves_kernel_history_and_resumes_wait_and_retry_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "source"
        (source / "db").mkdir(parents=True)
        (source / "files").mkdir()
        (source / "workspaces").mkdir()
        _materialize_required_stores(source)

        lifecycle = FakeLifecycleBackend()
        source_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(source / "db" / "kernel.sqlite3"),
        )
        source_coordination = SQLiteCoordinatorRepository(source / "db" / "coordination.sqlite3")
        source_coordinator = DurablePlanStepCoordinator(
            repository=source_coordination,
            kernel=source_kernel,
            coordinator_id="coordinator-before-backup",
        )
        t0 = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)

        wait_plan, wait_step = await _planned_single_step(source_kernel, key="wait")
        wait_projection = await source_coordinator.register_plan(wait_plan, (wait_step,))
        wait_run_id = wait_projection.steps[0].latest_run_id
        assert wait_run_id is not None
        wait = StepWait(
            wait_key="restore-deadline",
            wait_type=WaitType.DEADLINE,
            task_id=wait_plan.task_id,
            plan_id=wait_plan.id,
            step_id=wait_step.id,
            owner_ref=wait_step.owner_ref,
            project_id=wait_step.project_id,
            deadline_at=t0 + timedelta(seconds=30),
            created_at=t0,
        )
        wait_projection = await source_coordinator.wait_step(wait, now=t0)
        assert wait_projection.steps[0].status is StepStatus.WAITING
        assert wait_projection.steps[0].latest_run_id == wait_run_id

        retry_plan, retry_step = await _planned_single_step(source_kernel, key="retry")
        retry_projection = await source_coordinator.register_plan(
            retry_plan,
            (retry_step,),
            retry_policies={
                retry_step.id: StepRetryPolicy(
                    max_attempts=2,
                    initial_delay_seconds=30,
                    retryable_categories=("transient",),
                )
            },
        )
        first_retry_run_id = retry_projection.steps[0].latest_run_id
        assert first_retry_run_id is not None
        await source_kernel.record_run_outcome(
            idempotency_key="retry:first-run-failed",
            task_id=retry_plan.task_id,
            run_id=first_retry_run_id,
            status=RunStatus.FAILED,
        )
        retry_projection = await source_coordinator.observe_run(
            task_id=retry_plan.task_id,
            run_id=first_retry_run_id,
            failure_category="transient",
            observation_key="retry:first-failure-observed",
            now=t0,
        )
        assert retry_projection.steps[0].phase is CoordinationPhase.RETRY_SCHEDULED
        assert retry_projection.steps[0].retry_due_at == t0 + timedelta(seconds=30)

        wait_history_before = await source_kernel.history(wait_plan.task_id)
        retry_history_before = await source_kernel.history(retry_plan.task_id)
        assert sum(event.event_type == "run.created" for event in wait_history_before) == 1
        assert sum(event.event_type == "run.created" for event in retry_history_before) == 1

        backup = create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "backup",
            platform_version=__version__,
            quiesced=True,
        )
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
        )

        restored_kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=lifecycle,
            repository=SqliteKernelRepository(restored_root / "db" / "kernel.sqlite3"),
        )
        restored_coordinator = DurablePlanStepCoordinator(
            repository=SQLiteCoordinatorRepository(restored_root / "db" / "coordination.sqlite3"),
            kernel=restored_kernel,
            coordinator_id="coordinator-after-restore",
        )

        restored_wait_history = await restored_kernel.history(wait_plan.task_id)
        restored_retry_history = await restored_kernel.history(retry_plan.task_id)
        assert restored_wait_history == wait_history_before
        assert restored_retry_history == retry_history_before
        assert _event_ids(restored_wait_history) == _event_ids(wait_history_before)
        assert _event_ids(restored_retry_history) == _event_ids(retry_history_before)

        await restored_coordinator.process_due(now=t0 + timedelta(seconds=30))
        wait_after_due = restored_coordinator.projection(wait_plan.id).steps[0]
        retry_after_due = restored_coordinator.projection(retry_plan.id).steps[0]
        assert wait_after_due.status is StepStatus.RUNNING
        assert wait_after_due.latest_run_id == wait_run_id
        assert retry_after_due.status is StepStatus.RUNNING
        assert retry_after_due.current_attempt == 2
        second_retry_run_id = retry_after_due.latest_run_id
        assert second_retry_run_id is not None
        assert second_retry_run_id != first_retry_run_id

        wait_history_after_due = await restored_kernel.history(wait_plan.task_id)
        retry_history_after_due = await restored_kernel.history(retry_plan.task_id)
        assert wait_history_after_due == wait_history_before
        assert sum(event.event_type == "run.created" for event in retry_history_after_due) == 2
        retry_history_after_first_due = retry_history_after_due

        await restored_coordinator.process_due(now=t0 + timedelta(seconds=31))
        assert await restored_kernel.history(wait_plan.task_id) == wait_history_after_due
        assert await restored_kernel.history(retry_plan.task_id) == retry_history_after_first_due
        assert restored_coordinator.projection(wait_plan.id).steps[0].latest_run_id == wait_run_id
        assert (
            restored_coordinator.projection(retry_plan.id).steps[0].latest_run_id
            == second_retry_run_id
        )

        await restored_kernel.record_run_outcome(
            idempotency_key="wait:run-succeeded",
            task_id=wait_plan.task_id,
            run_id=wait_run_id,
            status=RunStatus.SUCCEEDED,
        )
        wait_final = await restored_coordinator.observe_run(
            task_id=wait_plan.task_id,
            run_id=wait_run_id,
            observation_key="wait:success-observed",
            now=t0 + timedelta(seconds=32),
        )
        wait_history_final = await restored_kernel.history(wait_plan.task_id)
        await restored_coordinator.observe_run(
            task_id=wait_plan.task_id,
            run_id=wait_run_id,
            observation_key="wait:success-observed",
            now=t0 + timedelta(seconds=33),
        )
        assert await restored_kernel.history(wait_plan.task_id) == wait_history_final
        assert wait_final.steps[0].status is StepStatus.SUCCEEDED
        assert (await restored_kernel.get_task(wait_plan.task_id)).status is TaskStatus.SUCCEEDED
        assert sum(event.event_type == "run.created" for event in wait_history_final) == 1
        assert sum(event.event_type == "task.succeeded" for event in wait_history_final) == 1
        assert _event_ids(wait_history_final[: len(wait_history_before)]) == _event_ids(
            wait_history_before
        )

        await restored_kernel.record_run_outcome(
            idempotency_key="retry:second-run-succeeded",
            task_id=retry_plan.task_id,
            run_id=second_retry_run_id,
            status=RunStatus.SUCCEEDED,
        )
        retry_final = await restored_coordinator.observe_run(
            task_id=retry_plan.task_id,
            run_id=second_retry_run_id,
            observation_key="retry:success-observed",
            now=t0 + timedelta(seconds=34),
        )
        retry_history_final = await restored_kernel.history(retry_plan.task_id)
        await restored_coordinator.observe_run(
            task_id=retry_plan.task_id,
            run_id=second_retry_run_id,
            observation_key="retry:success-observed",
            now=t0 + timedelta(seconds=35),
        )
        assert await restored_kernel.history(retry_plan.task_id) == retry_history_final
        assert retry_final.steps[0].status is StepStatus.SUCCEEDED
        assert (await restored_kernel.get_task(retry_plan.task_id)).status is TaskStatus.SUCCEEDED
        assert sum(event.event_type == "run.created" for event in retry_history_final) == 2
        assert sum(event.event_type == "task.succeeded" for event in retry_history_final) == 1
        assert _event_ids(retry_history_final[: len(retry_history_before)]) == _event_ids(
            retry_history_before
        )

    asyncio.run(scenario())
