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
from ai_multi_agent_platform.backup.manifest import backup_manifest_v1_schema
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.kernel import PlatformKernel, RecoveryReport, SqliteKernelRepository
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
        "agent-team-run-references:0:0:0",
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


def test_server_refuses_normal_service_while_restored_run_is_unresolved(
    tmp_path: Path, monkeypatch
) -> None:
    async def prepare() -> Path:
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
            objective="Remain unresolved after host loss",
            owner_type="service",
            owner_id="test",
        )
        await kernel.ready_task(idempotency_key="active:ready", task_id=task.task_id)
        run = await kernel.start_task(idempotency_key="active:start", task_id=task.task_id)
        assert run.status.value == "running"
        _ensure_required_sqlite_stores(source)

        backup = create_single_node_backup(
            data_dir=source,
            destination=tmp_path / "active-backup",
            platform_version=__version__,
            quiesced=True,
        )
        return restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "active-restored",
            expected_platform_version=__version__,
        )

    restored_root = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["serve"]) == 3
    report_path = restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT
    first_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert first_report["ready_for_service"] is False
    assert len(first_report["unresolved_run_ids"]) == 1
    assert not (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).exists()

    # The pending marker is gone, but the blocked report is retried and serving remains denied.
    assert server_main(["serve"]) == 3
    second_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert second_report["ready_for_service"] is False
    assert second_report["unresolved_run_ids"] == first_report["unresolved_run_ids"]