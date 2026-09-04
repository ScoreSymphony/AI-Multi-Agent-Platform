from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import (
    RESTORE_RECOVERY_DIR,
    RESTORE_RECOVERY_PENDING,
    RESTORE_RECOVERY_REPORT,
    BackupError,
    create_single_node_backup,
    reconcile_restored_single_node,
    restore_single_node_backup,
    verify_backup,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    JobRequirements,
    NodeRecord,
    NodeStatus,
    RegistrationRequest,
    ResourceSnapshot,
    WorkerRecord,
    WorkerStatus,
    prepare_registry_disaster_recovery,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus, new_id
from ai_multi_agent_platform.kernel import PlatformKernel, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

PASSWORD = "correct horse battery staple"

_REQUIRED_TEST_SQLITE_STORES = (
    "scopes.sqlite3",
    "files.sqlite3",
    "workspaces.sqlite3",
    "verification.sqlite3",
    "authentication.sqlite3",
    "authorization.sqlite3",
    "automation.sqlite3",
)


def _ensure_required_sqlite_stores(root: Path) -> None:
    database_dir = root / "db"
    for name in _REQUIRED_TEST_SQLITE_STORES:
        path = database_dir / name
        if path.exists():
            continue
        with sqlite3.connect(path):
            pass


def _source(tmp_path: Path, name: str = "source") -> Path:
    root = tmp_path / name
    (root / "db").mkdir(parents=True)
    (root / "files").mkdir()
    (root / "workspaces" / "ws-1").mkdir(parents=True)
    (root / "executor").mkdir()
    with sqlite3.connect(root / "db" / "kernel.sqlite3") as connection:
        connection.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO tasks VALUES ('task-1', 'keep me')")
        connection.execute("PRAGMA user_version = 7")
    with sqlite3.connect(root / "db" / "authentication.sqlite3") as connection:
        connection.execute(
            "CREATE TABLE auth_sessions (session_id TEXT PRIMARY KEY, token_verifier TEXT)"
        )
        connection.execute("INSERT INTO auth_sessions VALUES ('session-1', 'hashed')")
    _ensure_required_sqlite_stores(root)
    (root / "db" / "agents.json").write_text('{"agent-1": {}}', encoding="utf-8")
    (root / "files" / "artifact.txt").write_text("artifact", encoding="utf-8")
    (root / "workspaces" / "ws-1" / "notes.txt").write_text("notes", encoding="utf-8")
    (root / "executor" / "stale-job.txt").write_text("do not restore", encoding="utf-8")
    return root


def test_single_node_backup_restore_preserves_canonical_state_on_new_data_root(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        source_config = SingleNodeConfig(data_dir=tmp_path / "original", secure_cookie=False)
        deployment = build_single_node_deployment(source_config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        login = deployment.authentication.login("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="restore-project",
            name="Restore project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        task = await deployment.kernel.create_task(
            idempotency_key="restore-task",
            title="Preserve me",
            objective="Prove canonical IDs survive relocation",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        (source_config.files_dir / "durable.txt").write_text("durable", encoding="utf-8")
        (source_config.workspaces_dir / "manual").mkdir()
        (source_config.workspaces_dir / "manual" / "state.txt").write_text(
            "workspace", encoding="utf-8"
        )

        backup = create_single_node_backup(
            data_dir=source_config.data_dir,
            destination=tmp_path / "backup",
            platform_version=__version__,
            platform_commit="test-commit",
            deployment_metadata={"profile": "single-node"},
            quiesced=True,
        )
        payload_db = backup / "payload" / "db"
        assert not list(payload_db.glob("*-wal"))
        assert not list(payload_db.glob("*-shm"))
        assert not list(payload_db.glob("*-journal"))

        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "replacement-host" / "data",
            expected_platform_version=__version__,
        )
        restored_config = SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
        assert list(restored_config.executor_dir.iterdir()) == []
        assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).is_file()
        restored = build_single_node_deployment(restored_config)

        persisted_task = await restored.kernel.get_task(task.task_id)
        assert persisted_task.task_id == task.task_id
        same_project = restored.scopes.create_project(
            key="restore-project",
            name="ignored on idempotent retry",
            owner_type="user",
            owner_id=admin.user_id,
        )
        assert same_project.id == project.id
        assert restored.bootstrap_admin("admin", PASSWORD).user_id == admin.user_id
        assert (restored_config.files_dir / "durable.txt").read_text() == "durable"
        assert (restored_config.workspaces_dir / "manual" / "state.txt").read_text() == "workspace"
        assert not (restored_config.executor_dir / "stale-job.txt").exists()

        with sqlite3.connect(restored_config.database_dir / "authentication.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)
        assert login.session.token

        recovery = await reconcile_restored_single_node(
            data_dir=restored_root,
            kernel=restored.kernel,
        )
        assert recovery is not None
        assert recovery.unresolved_run_ids == ()
        assert not (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).exists()
        assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT).is_file()

        smoke = await restored.run_reference_smoke()
        assert smoke.task_status is TaskStatus.SUCCEEDED
        assert smoke.run_status is RunStatus.SUCCEEDED

    asyncio.run(scenario())


def test_manifest_matches_versioned_json_schema(tmp_path: Path) -> None:
    backup = create_single_node_backup(
        data_dir=_source(tmp_path),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads(Path("schemas/backup-manifest-v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(manifest)
    assert manifest["schema_migration"]["sqlite_user_versions"]["db/kernel.sqlite3"] == 7


def test_wal_sqlite_snapshot_is_self_contained(tmp_path: Path) -> None:
    source = _source(tmp_path)
    source_db = source / "db" / "kernel.sqlite3"
    with sqlite3.connect(source_db) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        connection.execute("INSERT INTO tasks VALUES ('task-wal', 'survives checkpoint')")

    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    snapshot = backup / "payload" / "db" / "kernel.sqlite3"
    assert snapshot.is_file()
    assert not snapshot.with_name(snapshot.name + "-wal").exists()
    assert not snapshot.with_name(snapshot.name + "-shm").exists()

    with sqlite3.connect(f"file:{snapshot}?mode=ro&immutable=1", uri=True) as connection:
        assert connection.execute(
            "SELECT title FROM tasks WHERE task_id = 'task-wal'"
        ).fetchone() == ("survives checkpoint",)


def test_backup_requires_quiescence_and_excludes_ephemeral_executor(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(BackupError, match="quiesced"):
        create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "backup",
            platform_version=__version__,
        )
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    verification = verify_backup(backup)
    excluded = {item["path"] for item in verification.manifest["excluded"]}
    assert "executor/" in excluded
    assert not (backup / "payload" / "executor").exists()


def test_checksum_corruption_and_missing_payload_are_detected(tmp_path: Path) -> None:
    backup = create_single_node_backup(
        data_dir=_source(tmp_path),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    artifact = backup / "payload" / "files" / "artifact.txt"
    artifact.write_text("tampered", encoding="utf-8")
    with pytest.raises(BackupError, match="size mismatch|checksum mismatch"):
        verify_backup(backup)

    shutil.rmtree(backup)
    backup = create_single_node_backup(
        data_dir=_source(tmp_path, "source-2"),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    (backup / "payload" / "files" / "artifact.txt").unlink()
    with pytest.raises(BackupError, match="missing"):
        verify_backup(backup)


def test_restore_rejects_unsafe_or_incompatible_backups(tmp_path: Path) -> None:
    backup = create_single_node_backup(
        data_dir=_source(tmp_path),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["path"] = "../escape"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="unsafe"):
        verify_backup(backup)

    shutil.rmtree(backup)
    backup = create_single_node_backup(
        data_dir=_source(tmp_path, "source-2"),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    with pytest.raises(BackupError, match="incompatible platform version"):
        restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "target",
            expected_platform_version="999.0.0",
        )

    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["manifest_schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="manifest schema"):
        verify_backup(backup)


def test_interrupted_restore_retry_secret_metadata_and_symlink_safety(tmp_path: Path) -> None:
    source = _source(tmp_path)
    with pytest.raises(BackupError, match="secret-looking"):
        create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "forbidden",
            platform_version=__version__,
            deployment_metadata={"api_token": "plaintext-must-not-leak"},
            quiesced=True,
        )

    outside = tmp_path / "outside-secret"
    outside.write_text("must not leak", encoding="utf-8")
    link = source / "files" / "outside-link"
    try:
        link.symlink_to(outside)
    except OSError:
        pass
    else:
        with pytest.raises(BackupError, match="symbolic links"):
            create_single_node_backup(
                data_dir=source,
                destination=tmp_path / "symlink-backup",
                platform_version=__version__,
                quiesced=True,
            )
        link.unlink()

    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    target = tmp_path / "restored"
    stale = tmp_path / ".restored.restore-partial"
    stale.mkdir()
    (stale / "junk").write_text("partial", encoding="utf-8")
    restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=target,
        expected_platform_version=__version__,
    )
    assert target.is_dir()
    assert not stale.exists()


def test_active_run_enters_canonical_reconciliation_after_disaster_restore(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "active-source"
        database_dir = source / "db"
        database_dir.mkdir(parents=True)
        (source / "files").mkdir()
        (source / "workspaces").mkdir()
        first_lifecycle = FakeLifecycleBackend()
        first = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=first_lifecycle,
            repository=SqliteKernelRepository(database_dir / "kernel.sqlite3"),
        )
        task = await first.create_task(
            idempotency_key="restore-active:create",
            title="Interrupted active run",
            objective="Require explicit reconciliation after host loss",
            owner_type="user",
            owner_id="tester",
        )
        await first.ready_task(
            idempotency_key="restore-active:ready",
            task_id=task.task_id,
        )
        run = await first.start_task(
            idempotency_key="restore-active:start",
            task_id=task.task_id,
        )
        assert run.status is RunStatus.RUNNING
        _ensure_required_sqlite_stores(source)

        backup = create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "active-backup",
            platform_version=__version__,
            quiesced=True,
        )
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "active-restored",
            expected_platform_version=__version__,
        )
        marker = restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING
        assert marker.is_file()

        restarted = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=SqliteKernelRepository(restored_root / "db" / "kernel.sqlite3"),
        )
        recovery = await reconcile_restored_single_node(
            data_dir=restored_root,
            kernel=restarted,
        )
        assert recovery is not None
        assert recovery.unresolved_run_ids == (run.run_id,)
        assert recovery.runs_checked == 1
        assert not marker.exists()
        assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT).is_file()

        recovered = await restarted.get_run(task.task_id, run.run_id)
        assert recovered.status is RunStatus.RUNNING
        assert recovered.recovery_required is True
        assert recovered.recovery_reason == "canonical_running_backend_not_found"
        history = await restarted.history(task.task_id)
        assert any(event.event_type == "run.recovery_required" for event in history)

        report = json.loads(recovery.report_path.read_text(encoding="utf-8"))
        assert report["unresolved_run_ids"] == [run.run_id]
        assert report["tasks"][0]["entries"][0]["disposition"] == (
            "orphaned_reconciliation_required"
        )
        assert (
            await reconcile_restored_single_node(data_dir=restored_root, kernel=restarted) is None
        )

    asyncio.run(scenario())


def test_disaster_registry_recovery_drops_stale_claims_then_reregisters_worker() -> None:
    registry = DistributedRegistry()
    node = NodeRecord(
        node_id=new_id("node"),
        display_name="restore-node",
        resources=ResourceSnapshot(
            cpu_cores_total=4.0,
            cpu_cores_available=4.0,
            ram_total_bytes=8_000,
            ram_available_bytes=8_000,
            storage_total_bytes=100_000,
            storage_available_bytes=100_000,
        ),
    )
    worker = WorkerRecord(
        worker_id=new_id("worker"),
        node_id=node.node_id,
        supported_executors=("reference",),
        concurrency_limit=2,
        active_jobs=1,
    )
    registry.register(RegistrationRequest(node=node, workers=(worker,)))
    reservation = registry.reserve(
        worker_job_id=new_id("worker_job"),
        worker_id=worker.worker_id,
        requirements=JobRequirements(cpu_cores_min=1.0),
    )
    assert reservation in registry.active_reservations()

    recovered = DistributedRegistry()
    recovered.restore_snapshot(prepare_registry_disaster_recovery(registry.snapshot()))

    assert recovered.get_node(node.node_id).status is NodeStatus.OFFLINE
    restored_worker = recovered.get_worker(worker.worker_id)
    assert restored_worker.status is WorkerStatus.OFFLINE
    assert restored_worker.active_jobs == 0
    assert recovered.active_reservations() == ()

    recovered.register(RegistrationRequest(node=node, workers=(worker,)))
    assert recovered.get_node(node.node_id).status is NodeStatus.ONLINE
    assert recovered.get_worker(worker.worker_id).status is WorkerStatus.HEALTHY
    assert recovered.get_worker(worker.worker_id).worker_id == worker.worker_id
    assert recovered.active_reservations() == ()


def test_restore_does_not_require_optional_adapter_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        source_config = SingleNodeConfig(data_dir=tmp_path / "adapter-source", secure_cookie=False)
        deployment = build_single_node_deployment(source_config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        task = await deployment.kernel.create_task(
            idempotency_key="optional-adapter:create",
            title="Portable canonical task",
            objective="Restore without requiring the former optional adapter runtime",
            owner_type="user",
            owner_id=admin.user_id,
        )
        backup = create_single_node_backup(
            data_dir=source_config.data_dir,
            destination=tmp_path / "adapter-backup",
            platform_version=__version__,
            deployment_metadata={
                "profile": "single-node",
                "optional_adapters": ["forge-unavailable-on-replacement-host"],
            },
            quiesced=True,
        )
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "adapter-restored",
            expected_platform_version=__version__,
        )
        restored = build_single_node_deployment(
            SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
        )
        persisted = await restored.kernel.get_task(task.task_id)
        assert persisted.task_id == task.task_id

    asyncio.run(scenario())
