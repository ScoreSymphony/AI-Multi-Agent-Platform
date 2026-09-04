from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import (
    RESTORE_RECOVERY_DIR,
    RESTORE_RECOVERY_PENDING,
    RESTORE_RECOVERY_REPORT,
    BackupError,
    create_single_node_backup,
    restore_single_node_backup,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main
from ai_multi_agent_platform.domain import RunStatus

PASSWORD = "correct horse battery staple"


def _backup(config: SingleNodeConfig, tmp_path: Path, name: str = "backup") -> Path:
    return create_single_node_backup(
        data_dir=config.data_dir,
        destination=tmp_path / name,
        platform_version=__version__,
        platform_commit="issue-40-recovery-completeness",
        quiesced=True,
    )


def test_backup_rejects_missing_eager_durable_store(tmp_path: Path) -> None:
    config = SingleNodeConfig(data_dir=tmp_path / "source", secure_cookie=False)
    build_single_node_deployment(config)
    missing = config.database_dir / "scopes.sqlite3"
    assert missing.is_file()
    missing.unlink()

    with pytest.raises(BackupError, match="required durable stores.*db/scopes.sqlite3"):
        _backup(config, tmp_path)
    assert not (tmp_path / "backup").exists()
    assert not (tmp_path / ".backup.partial").exists()


def test_operator_can_resolve_orphaned_restored_run_and_unblock_readiness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def prepare() -> tuple[Path, str, str]:
        config = SingleNodeConfig(data_dir=tmp_path / "source", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        task = await deployment.kernel.create_task(
            idempotency_key="recovery:create",
            title="Interrupted execution",
            objective="Exercise explicit operator disaster recovery",
            owner_type="user",
            owner_id=admin.user_id,
        )
        await deployment.kernel.ready_task(
            idempotency_key="recovery:ready",
            task_id=task.task_id,
        )
        run = await deployment.kernel.start_task(
            idempotency_key="recovery:start",
            task_id=task.task_id,
        )
        assert run.status is RunStatus.RUNNING

        backup = _backup(config, tmp_path)
        restored = restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "replacement" / "data",
            expected_platform_version=__version__,
            expected_platform_commit="issue-40-recovery-completeness",
        )
        return restored, task.task_id, run.run_id

    restored_root, task_id, run_id = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["recover-restore"]) == 3
    report_path = restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT
    blocked = json.loads(report_path.read_text(encoding="utf-8"))
    assert blocked["ready_for_service"] is False
    assert blocked["unresolved_run_ids"] == [run_id]

    assert (
        server_main(
            [
                "resolve-restore-run",
                "--task-id",
                task_id,
                "--run-id",
                run_id,
                "--outcome",
                "failed",
                "--reason",
                "replacement host cannot prove the lost execution completed",
            ]
        )
        == 0
    )

    ready = json.loads(report_path.read_text(encoding="utf-8"))
    assert ready["ready_for_service"] is True
    assert ready["unresolved_run_ids"] == []
    assert "agent-team-run-references:0:0:0" in ready["validation_checks"]
    assert "conversation-message-references:0:0" in ready["validation_checks"]

    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
    )
    run = asyncio.run(deployment.kernel.get_run(task_id, run_id))
    assert run.status is RunStatus.FAILED
    assert run.recovery_required is False
    assert run.output["recovery_source"] == "operator-disaster-recovery"

    # A ready restore report is no longer reopened as blocked on a later operator pass.
    assert server_main(["recover-restore"]) == 0


def test_restore_gate_rejects_broken_conversation_project_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def prepare() -> Path:
        config = SingleNodeConfig(data_dir=tmp_path / "source", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        deployment.bootstrap_admin("admin", PASSWORD)
        await deployment.conversations.create_conversation(
            title="Broken restored reference",
            owner_ref="user:test",
            project_id="project_missingafterrestore",
        )
        backup = _backup(config, tmp_path)
        return restore_single_node_backup(
            backup_dir=backup,
            target_data_dir=tmp_path / "restored",
            expected_platform_version=__version__,
        )

    restored_root = asyncio.run(prepare())
    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")

    assert server_main(["recover-restore"]) == 3
    assert (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_PENDING).is_file()
    assert not (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT).exists()
