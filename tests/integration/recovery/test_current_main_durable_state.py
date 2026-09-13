from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import create_single_node_backup, restore_single_node_backup
from ai_multi_agent_platform.backup.cli import main as backup_main
from ai_multi_agent_platform.backup.inventory import optional_single_node_store_paths
from ai_multi_agent_platform.backup.provenance import BUILD_COMMIT_ENV, require_build_commit
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.notifications import (
    Notification,
    NotificationCategory,
    NotificationSeverity,
    RecipientRef,
    RecipientType,
    SourceRef,
    SqliteNotificationRepository,
)
from ai_multi_agent_platform.templates import TemplateConfiguration, TemplateContent, TemplateType

PASSWORD = "correct horse battery staple"


def _deployment(tmp_path: Path, name: str):
    config = SingleNodeConfig(data_dir=tmp_path / name, secure_cookie=False)
    return config, build_single_node_deployment(config)


def _backup_restore(config: SingleNodeConfig, tmp_path: Path, name: str) -> Path:
    backup = create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / f"{name}-backup",
        platform_version=__version__,
        platform_commit="issue-40-current-main",
        quiesced=True,
    )
    return restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / f"{name}-restored",
        expected_platform_version=__version__,
        expected_platform_commit="issue-40-current-main",
    )


def _recover(restored_root: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")
    return server_main(["recover-restore"])


async def _notification_fixture(tmp_path: Path, name: str) -> tuple[SingleNodeConfig, str]:
    config, deployment = _deployment(tmp_path, name)
    admin = deployment.bootstrap_admin("admin", PASSWORD)
    project = deployment.scopes.create_project(
        key=f"{name}-project",
        name="Notification restore project",
        owner_type="user",
        owner_id=admin.user_id,
    )
    task = await deployment.kernel.create_task(
        idempotency_key=f"{name}:task",
        title="Notification restore fixture",
        objective="Provide canonical notification references",
        owner_type="user",
        owner_id=admin.user_id,
        project_id=project.id,
    )
    notification = Notification(
        category=NotificationCategory.TASK,
        severity=NotificationSeverity.INFO,
        title="Canonical task notification",
        summary={},
        recipient=RecipientRef(RecipientType.USER, admin.user_id),
        source=SourceRef("task", task.task_id),
        project_id=project.id,
        task_id=task.task_id,
    )
    await SqliteNotificationRepository(config.database_dir / "notifications.sqlite3").save(
        notification
    )
    return config, notification.id


def test_template_store_is_in_authoritative_backup_inventory() -> None:
    assert "db/templates.json" in optional_single_node_store_paths()


def test_restore_blocks_notification_missing_project_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, notification_id = asyncio.run(_notification_fixture(tmp_path, "notification-project"))
    restored_root = _backup_restore(config, tmp_path, "notification-project")
    database = restored_root / "db" / "notifications.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload FROM notifications WHERE id = ?", (notification_id,)
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        payload["project_id"] = new_id("project")
        connection.execute(
            "UPDATE notifications SET payload = ? WHERE id = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), notification_id),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_notification_delivery_missing_notification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = asyncio.run(_notification_fixture(tmp_path, "notification-delivery"))
    restored_root = _backup_restore(config, tmp_path, "notification-delivery")
    missing_notification_id = new_id("notification")
    attempt_id = new_id("notification_delivery")
    restored = build_single_node_deployment(
        SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
    )
    admin_id = next(iter(restored.authentication.store.users))
    payload = {
        "id": attempt_id,
        "notification_id": missing_notification_id,
        "recipient_type": "user",
        "recipient_id": admin_id,
    }
    with sqlite3.connect(restored_root / "db" / "notifications.sqlite3") as connection:
        connection.execute(
            "INSERT INTO notification_delivery_attempts("
            "id, notification_id, channel, attempt, attempted_at, payload"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                missing_notification_id,
                "fixture",
                1,
                datetime.now(UTC).isoformat(),
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_notification_cursor_missing_canonical_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = asyncio.run(_notification_fixture(tmp_path, "notification-cursor"))
    restored_root = _backup_restore(config, tmp_path, "notification-cursor")
    with sqlite3.connect(restored_root / "db" / "notifications.sqlite3") as connection:
        connection.execute(
            "INSERT INTO notification_processed_events(event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (new_id("event"), "task.created", datetime.now(UTC).isoformat()),
        )
        connection.commit()

    assert _recover(restored_root, monkeypatch) == 3


def test_restore_blocks_template_missing_project_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, deployment = _deployment(tmp_path, "template-source")
    admin = deployment.bootstrap_admin("admin", PASSWORD)
    project = deployment.scopes.create_project(
        key="template-project",
        name="Template restore project",
        owner_type="user",
        owner_id=admin.user_id,
    )
    deployment.templates.templates.create_draft(
        owner_ref=OwnerRef(type="user", id=admin.user_id),
        project_id=project.id,
        content=TemplateContent(
            name="Restore fixture",
            description="Durable template reference validation",
            template_type=TemplateType.COMPOSITE,
            configuration=TemplateConfiguration(payload={"fixture": "value"}),
        ),
    )
    restored_root = _backup_restore(config, tmp_path, "template")
    template_path = restored_root / "db" / "templates.json"
    document = json.loads(template_path.read_text(encoding="utf-8"))
    missing_project_id = new_id("project")
    assert document["templates"]
    assert document["revisions"]
    document["templates"][0]["project_id"] = missing_project_id
    for revision in document["revisions"]:
        revision["project_id"] = missing_project_id
    template_path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert _recover(restored_root, monkeypatch) == 3


def test_operator_backup_cli_records_environment_build_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, _ = _deployment(tmp_path, "provenance-source")
    monkeypatch.setenv(BUILD_COMMIT_ENV, "build-commit-from-environment")
    destination = tmp_path / "provenance-backup"

    assert (
        backup_main(
            [
                "create",
                "--data-dir",
                str(config.data_dir),
                "--destination",
                str(destination),
                "--quiesced",
            ]
        )
        == 0
    )
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["platform"]["commit"] == "build-commit-from-environment"
    assert require_build_commit() == "build-commit-from-environment"


def test_operator_restore_requires_explicit_opt_in_for_unpinned_legacy_backup(
    tmp_path: Path,
) -> None:
    config, _ = _deployment(tmp_path, "legacy-source")
    backup = create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / "legacy-backup",
        platform_version=__version__,
        platform_commit=None,
        quiesced=True,
    )

    with pytest.raises(SystemExit) as exc_info:
        backup_main(
            [
                "restore",
                str(backup),
                "--target-data-dir",
                str(tmp_path / "blocked-legacy-restore"),
            ]
        )
    assert exc_info.value.code == 2

    assert (
        backup_main(
            [
                "restore",
                str(backup),
                "--target-data-dir",
                str(tmp_path / "allowed-legacy-restore"),
                "--allow-unpinned-backup",
            ]
        )
        == 0
    )
