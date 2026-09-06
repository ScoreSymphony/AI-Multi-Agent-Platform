from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.backup import BackupVerification
from ai_multi_agent_platform.coordination import (
    COORDINATOR_MIGRATION_REVISION,
    COORDINATOR_SCHEMA_VERSION,
    CoordinationPhase,
    SQLiteCoordinatorRepository,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitType,
    coordinator_migration_plan,
    inspect_coordinator_store,
    migrate_coordinator_store,
)
from ai_multi_agent_platform.coordination.sqlite_repository import (
    SQLiteCoordinatorRepository as LegacySQLiteCoordinatorRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, Plan, Step, StepStatus, new_id
from ai_multi_agent_platform.upgrade import (
    CoordinatorAwareMigrationRunner,
    CoordinatorAwareUpgradePreflight,
    FormatTranslatorRegistry,
    JsonMigrationHistoryStore,
    JsonUpgradeHistoryStore,
    JsonVersionStateStore,
    MaintenanceStateStore,
    MigrationRegistry,
    PreflightRequest,
    UpgradeService,
    current_release_versions,
)


def _legacy_coordination_fixture(root: Path) -> tuple[Plan, tuple[Step, ...], int]:
    database = root / "db" / "coordination.sqlite3"
    owner = OwnerRef(type="user", id="upgrade-user")
    plan = Plan(
        task_id=new_id("task"),
        owner_ref=owner,
        active=True,
        project_id=new_id("project"),
    )
    waiting = Step(
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
        depends_on=(predecessor.id, waiting.id),
    )
    retry = Step(
        plan_id=plan.id,
        title="pending retry",
        owner_ref=owner,
        project_id=plan.project_id,
        status=StepStatus.FAILED,
    )
    now = datetime(2026, 9, 6, 10, 0, tzinfo=UTC)
    wait = StepWait(
        wait_key="upgrade-wait",
        wait_type=WaitType.DEADLINE,
        task_id=plan.task_id,
        plan_id=plan.id,
        step_id=waiting.id,
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
            step_id=waiting.id,
            phase=CoordinationPhase.WAITING,
            wait=wait,
            latest_run_id=new_id("run"),
            current_attempt=1,
            processed_keys=("event:already-seen",),
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
            step_id=retry.id,
            phase=CoordinationPhase.RETRY_SCHEDULED,
            current_attempt=1,
            retry_policy=StepRetryPolicy(
                max_attempts=3,
                initial_delay_seconds=30,
                retryable_categories=("transient",),
            ),
            retry_due_at=now + timedelta(seconds=30),
        ),
    )
    legacy = LegacySQLiteCoordinatorRepository(database)
    legacy.create_plan(plan, (waiting, predecessor, barrier, retry), records)
    claim = legacy.acquire_claim(
        step_id=barrier.id,
        owner_id="pre-upgrade-coordinator",
        ttl=timedelta(minutes=5),
        now=now,
    )
    assert claim is not None
    return plan, (waiting, predecessor, barrier, retry), claim.fence


def _backup_verifier(version: str) -> Callable[[Path], BackupVerification]:
    def verify(path: Path) -> BackupVerification:
        return BackupVerification(
            backup_dir=path,
            files_checked=1,
            bytes_checked=1,
            manifest={"platform": {"version": version}},
        )

    return verify


def test_explicit_v1_to_v2_migration_preserves_runtime_state_and_invalidates_claims(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    plan, steps, previous_fence = _legacy_coordination_fixture(data_dir)
    path = data_dir / "db" / "coordination.sqlite3"

    metadata = inspect_coordinator_store(path)
    assert metadata is not None
    assert metadata.schema_version == 1
    assert coordinator_migration_plan(path) == (COORDINATOR_MIGRATION_REVISION,)
    with pytest.raises(RuntimeError, match="explicit platform upgrade"):
        SQLiteCoordinatorRepository(path)

    assert migrate_coordinator_store(path) == (COORDINATOR_MIGRATION_REVISION,)
    assert migrate_coordinator_store(path) == ()

    migrated = inspect_coordinator_store(path)
    assert migrated is not None
    assert migrated.schema_version == COORDINATOR_SCHEMA_VERSION
    assert migrated.migration_revision == COORDINATOR_MIGRATION_REVISION

    repository = SQLiteCoordinatorRepository(path)
    assert repository.get_plan(plan.id).plan == plan
    records = {record.step_id: record for record in repository.list_step_records(plan.id)}
    waiting, predecessor, barrier, retry = steps
    assert records[waiting.id].phase is CoordinationPhase.WAITING
    assert records[waiting.id].wait is not None
    assert records[waiting.id].processed_keys == ("event:already-seen",)
    assert records[barrier.id].satisfied_dependency_ids == (predecessor.id,)
    assert records[retry.id].phase is CoordinationPhase.RETRY_SCHEDULED
    assert records[retry.id].current_attempt == 1
    assert records[retry.id].retry_due_at == datetime(2026, 9, 6, 10, 0, tzinfo=UTC) + timedelta(
        seconds=30
    )

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM coordinator_claims").fetchone() == (0,)
        fence = connection.execute(
            "SELECT fence FROM coordinator_fences WHERE step_id = ?", (barrier.id,)
        ).fetchone()
    assert fence == (previous_fence,)

    next_claim = repository.acquire_claim(
        step_id=barrier.id,
        owner_id="post-upgrade-coordinator",
        ttl=timedelta(minutes=5),
        now=datetime(2026, 9, 6, 11, 0, tzinfo=UTC),
    )
    assert next_claim is not None
    assert next_claim.fence > previous_fence


def test_41_preflight_and_upgrade_service_migrate_coordinator_store_explicitly(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    (data_dir / "db").mkdir(parents=True)
    plan, _, _ = _legacy_coordination_fixture(data_dir)
    backup_dir = tmp_path / "verified-backup"
    backup_dir.mkdir()

    versions = current_release_versions()
    migration_history = JsonMigrationHistoryStore.for_data_dir(data_dir)
    registry = MigrationRegistry()
    preflight = CoordinatorAwareUpgradePreflight(
        registry,
        migration_history,
        portable_translators=FormatTranslatorRegistry(versions.portable_format),
        template_translators=FormatTranslatorRegistry(versions.template_schema),
        backup_verifier=_backup_verifier(versions.platform_release),
    )
    request_without_backup = PreflightRequest(
        data_dir=data_dir,
        current=versions,
        target=versions,
    )
    blocked = preflight.run(request_without_backup)
    assert blocked.ok is False
    assert blocked.backup_required is True
    assert blocked.maintenance_required is True
    assert any(check.code == "coordination.migration.backup_required" for check in blocked.checks)

    request = PreflightRequest(
        data_dir=data_dir,
        current=versions,
        target=versions,
        backup_dir=backup_dir,
    )
    report = preflight.run(request)
    assert report.ok is True
    assert report.backup_required is True
    assert report.maintenance_required is True
    assert any(check.code == "coordination.migration.required" for check in report.checks)

    version_state = JsonVersionStateStore.for_data_dir(data_dir)
    version_state.initialize(versions)
    maintenance = MaintenanceStateStore.for_data_dir(data_dir)
    service = UpgradeService(
        migrations=registry,
        runner=CoordinatorAwareMigrationRunner(migration_history),
        preflight=preflight,
        version_state=version_state,
        maintenance=maintenance,
        history=JsonUpgradeHistoryStore.for_data_dir(data_dir),
    )
    service.apply(request, quiesced=True)

    assert maintenance.active() is False
    metadata = inspect_coordinator_store(data_dir / "db" / "coordination.sqlite3")
    assert metadata is not None and metadata.current
    assert (
        SQLiteCoordinatorRepository(data_dir / "db" / "coordination.sqlite3").get_plan(plan.id).plan
        == plan
    )
