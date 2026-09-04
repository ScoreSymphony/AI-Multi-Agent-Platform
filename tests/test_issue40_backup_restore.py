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
    BackupError,
    create_single_node_backup,
    restore_single_node_backup,
    verify_backup,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment

PASSWORD = "correct horse battery staple"


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
        restored_root = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "replacement-host" / "data",
            expected_platform_version=__version__,
        )
        restored_config = SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
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
        assert list(restored_config.executor_dir.iterdir()) == []

        with sqlite3.connect(restored_config.database_dir / "authentication.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM auth_sessions").fetchone() == (0,)
        assert login.session.token

    asyncio.run(scenario())


def test_manifest_matches_versioned_json_schema(tmp_path: Path) -> None:
    backup = create_single_node_backup(
        data_dir=_source(tmp_path),
        destination=tmp_path / "backup",
        platform_version=__version__,
        quiesced=True,
    )
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    schema = json.loads(
        Path("schemas/backup-manifest-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(manifest)
    assert manifest["schema_migration"]["sqlite_user_versions"]["db/kernel.sqlite3"] == 7


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
