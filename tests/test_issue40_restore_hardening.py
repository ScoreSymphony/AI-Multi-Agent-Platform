from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import (
    RESTORE_RECOVERY_DIR,
    RESTORE_RECOVERY_PENDING,
    RESTORE_RECOVERY_REPORT,
    BackupError,
    RestoreValidationError,
    create_single_node_backup,
    reconcile_restored_single_node,
    restore_single_node_backup,
    validate_restored_single_node,
    verify_backup,
)
from ai_multi_agent_platform.backup.inventory import required_single_node_store_paths
from ai_multi_agent_platform.backup.manifest import backup_manifest_v1_schema
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryReport, SqliteKernelRepository
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

PASSWORD = "correct horse battery staple"


def _deployment(tmp_path: Path, name: str = "source"):
    config = SingleNodeConfig(data_dir=tmp_path / name, secure_cookie=False)
    return config, build_single_node_deployment(config)


def _backup(config: SingleNodeConfig, tmp_path: Path, name: str = "backup") -> Path:
    return create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / name,
        platform_version=__version__,
        platform_commit="issue-40-hardening",
        quiesced=True,
    )


def _materialize_required_stores(root: Path) -> None:
    for relative in required_single_node_store_paths():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with sqlite3.connect(path):
                pass


def test_packaged_manifest_schema_matches_normative_repository_schema() -> None:
    normative = json.loads(
        Path("schemas/backup-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    assert backup_manifest_v1_schema() == normative


def test_runtime_verify_rejects_manifest_that_fails_json_schema(tmp_path: Path) -> None:
    config, _ = _deployment(tmp_path)
    backup = _backup(config, tmp_path)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("restore_policy")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError, match="manifest schema validation failed"):
        verify_backup(backup)


def test_backup_rejects_incomplete_source_and_manifest_scope(tmp_path: Path) -> None:
    incomplete = tmp_path / "incomplete"
    (incomplete / "db").mkdir(parents=True)
    (incomplete / "files").mkdir()
    with sqlite3.connect(incomplete / "db" / "kernel.sqlite3") as connection:
        connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY)")

    with pytest.raises(BackupError, match="workspaces"):
        create_single_node_backup(
            data_dir=incomplete,
            destination=tmp_path / "incomplete-backup",
            platform_version=__version__,
            quiesced=True,
        )

    config, _ = _deployment(tmp_path, "complete")
    backup = _backup(config, tmp_path, "complete-backup")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["included_components"].remove("workspaces")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError, match="missing required single-node components"):
        verify_backup(backup)


def test_backup_rejects_missing_required_durable_store(tmp_path: Path) -> None:
    config, _ = _deployment(tmp_path)
    (config.database_dir / "scopes.sqlite3").unlink()

    with pytest.raises(BackupError, match=r"required durable store: db/scopes\.sqlite3"):
        _backup(config, tmp_path)


def test_verify_detects_sqlite_schema_metadata_mismatch(tmp_path: Path) -> None:
    config, _ = _deployment(tmp_path)
    backup = _backup(config, tmp_path)
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    versions = manifest["schema_migration"]["sqlite_user_versions"]
    versions["db/kernel.sqlite3"] = int(versions["db/kernel.sqlite3"]) + 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupError, match="SQLite schema version mismatch"):
        verify_backup(backup)


def test_restore_can_require_exact_platform_commit(tmp_path: Path) -> None:
    config, _ = _deployment(tmp_path)
    backup = _backup(config, tmp_path)

    with pytest.raises(BackupError, match="incompatible platform commit"):
        restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
            expected_platform_commit="different-build",
        )


def test_full_operator_recovery_runs_integrity_and_health_gate(tmp_path: Path, monkeypatch) -> None:
    async def prepare() -> Path:
        config, deployment = _deployment(tmp_path)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="hardening-project",
            name="Hardening project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.create_task(
            idempotency_key="hardening-task",
            title="Preserve canonical references",
            objective="Exercise post-restore readiness validation",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        backup = _backup(config, tmp_path)
        return restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
            expected_platform_commit="issue-40-hardening",
        )

    restored_root = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["recover-restore"]) == 0
    report_path = restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ready_for_service"] is True
    assert report["unresolved_run_ids"] == []
    assert report["validation_checks"] == [
        "durable-state-layout-and-sqlite-integrity",
        "canonical-task-run-references:1:0",
        "workspace-project-references:0",
        "durable-file-metadata-and-bytes:0",
        "agent-team-run-references:0",
        "conversation-message-references:0:0",
        "control-plane-provider-health-ready",
    ]
    assert not (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).exists()


def test_integrity_gate_rejects_missing_canonical_project_reference(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, deployment = _deployment(tmp_path)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="project",
            name="Project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.create_task(
            idempotency_key="task",
            title="Task",
            objective="Reference a project that will be deliberately removed",
            owner_type="user",
            owner_id=admin.user_id,
            project_id=project.id,
        )
        backup = _backup(config, tmp_path)
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
        )

        with sqlite3.connect(restored_root / "db" / "scopes.sqlite3") as connection:
            connection.execute("DELETE FROM scope_projects WHERE project_id = ?", (project.id,))
            connection.commit()

        restored = build_single_node_deployment(
            SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
        )

        async def validate(
            reports: tuple[RecoveryReport, ...], restore_metadata: dict[str, object]
        ) -> tuple[str, ...]:
            return await validate_restored_single_node(
                data_dir=restored_root,
                kernel=restored.kernel,
                scopes=restored.scopes,
                reports=reports,
                restore_metadata=restore_metadata,
                health_probe=restored.control_plane.health,
            )

        with pytest.raises(RestoreValidationError, match="references missing project"):
            await reconcile_restored_single_node(
                data_dir=restored_root,
                kernel=restored.kernel,
                validation=validate,
                retry_blocked=True,
            )
        assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).is_file()

    asyncio.run(scenario())


def test_composed_conversation_integrity_rejects_missing_project(tmp_path: Path, monkeypatch) -> None:
    async def prepare() -> tuple[Path, str]:
        config, deployment = _deployment(tmp_path)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        project = deployment.scopes.create_project(
            key="conversation-project",
            name="Conversation project",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.conversations.create_conversation(
            title="Persistent conversation",
            owner_ref=admin.user_id,
            project_id=project.id,
        )
        backup = _backup(config, tmp_path)
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "conversation-restored",
            expected_platform_version=__version__,
        )
        return restored_root, project.id

    restored_root, project_id = asyncio.run(prepare())
    with sqlite3.connect(restored_root / "db" / "scopes.sqlite3") as connection:
        connection.execute("DELETE FROM scope_projects WHERE project_id = ?", (project_id,))
        connection.commit()

    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")
    assert server_main(["recover-restore"]) == 3
    assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).is_file()


def test_integrity_gate_rejects_unready_provider_health(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, _ = _deployment(tmp_path)
        backup = _backup(config, tmp_path)
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
        )
        restored = build_single_node_deployment(
            SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
        )

        async def unready_health():
            return {"status": "healthy", "ready": False, "api_version": "v1", "providers": []}

        async def validate(
            reports: tuple[RecoveryReport, ...], restore_metadata: dict[str, object]
        ) -> tuple[str, ...]:
            return await validate_restored_single_node(
                data_dir=restored_root,
                kernel=restored.kernel,
                scopes=restored.scopes,
                reports=reports,
                restore_metadata=restore_metadata,
                health_probe=unready_health,
            )

        with pytest.raises(RestoreValidationError, match="health/readiness"):
            await reconcile_restored_single_node(
                data_dir=restored_root,
                kernel=restored.kernel,
                validation=validate,
                retry_blocked=True,
            )
        assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).is_file()

    asyncio.run(scenario())


def test_server_refuses_then_operator_resolves_orphaned_restored_run(
    tmp_path: Path, monkeypatch
) -> None:
    async def prepare() -> tuple[Path, str, str]:
        source = tmp_path / "active-source"
        (source / "db").mkdir(parents=True)
        (source / "files").mkdir()
        (source / "workspaces").mkdir()
        kernel = PlatformKernel(
            orchestrator=FakeOrchestrator(),
            lifecycle=FakeLifecycleBackend(),
            repository=SqliteKernelRepository(source / "db" / "kernel.sqlite3"),
        )
        task = await kernel.create_task(
            idempotency_key="active:create",
            title="Active task",
            objective="Require explicit operator recovery after host loss",
            owner_type="service",
            owner_id="test",
        )
        await kernel.ready_task(idempotency_key="active:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="active:start", task_id=task.task_id)
        assert run.status.value == "running"
        _materialize_required_stores(source)

        backup = create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "active-backup",
            platform_version=__version__,
            quiesced=True,
        )
        restored = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "active-restored",
            expected_platform_version=__version__,
        )
        return restored, task.task_id, run.run_id

    restored_root, task_id, run_id = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["serve"]) == 3
    report_path = restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT
    first_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_report["ready_for_service"] is False
    assert first_report["unresolved_run_ids"] == [run_id]
    assert not (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).exists()

    # The pending marker is gone, but the blocked report remains authoritative.
    assert server_main(["serve"]) == 3
    assert (
        server_main(
            [
                "resolve-restore-run",
                "--task-id",
                task_id,
                "--run-id",
                run_id,
                "--resolution",
                "failed",
                "--reason",
                "execution backend was lost with the original host",
            ]
        )
        == 0
    )

    final_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert final_report["ready_for_service"] is True
    assert final_report["unresolved_run_ids"] == []
    assert server_main(["recover-restore"]) == 0

    restored = build_single_node_deployment(
        SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
    )
    recovered_run = asyncio.run(restored.kernel.get_run(task_id, run_id))
    assert recovered_run.status.value == "failed"
    assert recovered_run.recovery_required is False


def test_restore_run_resolution_rejects_run_not_in_blocked_report(tmp_path: Path, monkeypatch) -> None:
    async def prepare() -> tuple[Path, str, str]:
        config, deployment = _deployment(tmp_path)
        task = await deployment.kernel.create_task(
            idempotency_key="ordinary:create",
            title="Ordinary task",
            objective="Never become an operator recovery target",
            owner_type="service",
            owner_id="test",
        )
        await deployment.kernel.ready_task(
            idempotency_key="ordinary:ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.create_run(
            idempotency_key="ordinary:run",
            task_id=task.task_id,
        )
        backup = _backup(config, tmp_path)
        restored = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "ordinary-restored",
            expected_platform_version=__version__,
        )
        return restored, task.task_id, run.run_id

    restored_root, task_id, run_id = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")
    assert server_main(["recover-restore"]) == 0
    assert (
        server_main(
            [
                "resolve-restore-run",
                "--task-id",
                task_id,
                "--run-id",
                run_id,
                "--resolution",
                "cancelled",
                "--reason",
                "must not be accepted",
            ]
        )
        == 3
    )
