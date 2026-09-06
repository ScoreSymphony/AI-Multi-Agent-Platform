from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import create_single_node_backup, restore_single_node_backup
from ai_multi_agent_platform.backup.inventory import required_single_node_store_paths
from ai_multi_agent_platform.coordination import (
    CoordinationPhase,
    SQLiteCoordinatorRepository,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitType,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, new_id


def _materialize_required_stores(root: Path) -> None:
    for relative in required_single_node_store_paths():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with sqlite3.connect(path):
                pass


def test_backup_restore_preserves_wait_partial_fan_in_and_pending_retry_once(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    (source / "db").mkdir(parents=True)
    (source / "files").mkdir()
    (source / "workspaces").mkdir()
    _materialize_required_stores(source)

    owner = OwnerRef(type="user", id="coordination-backup-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    wait_step = Step(
        plan_id=plan.id,
        title="waiting",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.WAITING,
    )
    predecessor = Step(
        plan_id=plan.id,
        title="completed predecessor",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.SUCCEEDED,
    )
    barrier = Step(
        plan_id=plan.id,
        title="partial fan-in",
        owner_ref=owner,
        project_id=plan.project_id,
        depends_on=(predecessor.id, wait_step.id),
    )
    retry_step = Step(
        plan_id=plan.id,
        title="pending retry",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.FAILED,
    )
    now = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    wait = StepWait(
        wait_key="backup-deadline",
        wait_type=WaitType.DEADLINE,
        task_id=plan.task_id,
        plan_id=plan.id,
        step_id=wait_step.id,
        owner_ref=owner,
        project_id=plan.project_id,
        deadline_at=now + timedelta(minutes=15),
        created_at=now,
    )
    records = (
        StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=wait_step.id,
            phase=CoordinationPhase.WAITING,
            wait=wait,
            latest_run_id=new_id("run"),
        ),
        StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=predecessor.id,
            phase=CoordinationPhase.TERMINAL,
        ),
        StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=barrier.id,
            phase=CoordinationPhase.BLOCKED,
            dependency_ids=barrier.depends_on,
            satisfied_dependency_ids=(predecessor.id,),
        ),
        StepCoordinationRecord(
            task_id=plan.task_id,
            plan_id=plan.id,
            plan_revision=plan.revision,
            step_id=retry_step.id,
            phase=CoordinationPhase.RETRY_SCHEDULED,
            current_attempt=1,
            retry_policy=StepRetryPolicy(
                max_attempts=2,
                initial_delay_seconds=30,
                retryable_categories=("transient",),
            ),
            retry_due_at=now + timedelta(seconds=30),
        ),
    )
    repository = SQLiteCoordinatorRepository(source / "db" / "coordination.sqlite3")
    repository.create_plan(
        plan,
        (wait_step, predecessor, barrier, retry_step),
        records,
    )

    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    assert (backup / "payload" / "db" / "coordination.sqlite3").is_file()

    restored_root = restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / "restored",
        expected_platform_version=__version__,
    )
    restored = SQLiteCoordinatorRepository(restored_root / "db" / "coordination.sqlite3")
    restored_records = {record.step_id: record for record in restored.list_step_records(plan.id)}

    assert len(restored_records) == 4
    restored_wait = restored_records[wait_step.id]
    assert restored_wait.phase is CoordinationPhase.WAITING
    assert restored_wait.wait == wait

    restored_barrier = restored_records[barrier.id]
    assert restored_barrier.phase is CoordinationPhase.BLOCKED
    assert restored_barrier.dependency_ids == barrier.depends_on
    assert restored_barrier.satisfied_dependency_ids == (predecessor.id,)

    restored_retry = restored_records[retry_step.id]
    assert restored_retry.phase is CoordinationPhase.RETRY_SCHEDULED
    assert restored_retry.current_attempt == 1
    assert restored_retry.retry_due_at == now + timedelta(seconds=30)

    restored_steps = {step.id: step for step in restored.get_plan(plan.id).steps}
    assert restored_steps == {
        step.id: step for step in (wait_step, predecessor, barrier, retry_step)
    }
