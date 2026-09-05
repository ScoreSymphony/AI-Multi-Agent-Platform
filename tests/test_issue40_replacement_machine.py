from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ai_multi_agent_platform import __version__
from ai_multi_agent_platform.backup import (
    RESTORE_RECOVERY_DIR,
    RESTORE_RECOVERY_REPORT,
    create_single_node_backup,
    restore_single_node_backup,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import main as server_main

PASSWORD = "correct horse battery staple"


def test_clean_replacement_machine_restore_preserves_canonical_history(
    tmp_path: Path, monkeypatch
) -> None:
    async def prepare_source() -> tuple[Path, str, str, str]:
        source_root = tmp_path / "source-host-a" / "platform-state"
        source = build_single_node_deployment(
            SingleNodeConfig(data_dir=source_root, secure_cookie=False)
        )
        admin = source.bootstrap_admin("admin", PASSWORD)
        smoke = await source.run_reference_smoke()

        backup = create_single_node_backup(
            data_dir=source_root,
            destination=tmp_path / "portable-backup",
            platform_version=__version__,
            platform_commit="issue-40-replacement-machine",
            deployment_metadata={
                "profile": "single-node",
                "source_environment": "source-host-a",
            },
            quiesced=True,
        )
        return backup, admin.user_id, smoke.task_id, smoke.run_id

    backup, user_id, task_id, run_id = asyncio.run(prepare_source())
    replacement_root = tmp_path / "replacement-host-b" / "relocated-platform-state"
    assert not replacement_root.exists()

    restored_root = restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=replacement_root,
        expected_platform_version=__version__,
        expected_platform_commit="issue-40-replacement-machine",
    )
    assert restored_root == replacement_root.resolve()

    # The archive records portable deployment metadata, never the machine-local source data path.
    manifest_text = (backup / "manifest.json").read_text(encoding="utf-8")
    deployment_metadata = json.loads(
        (backup / "payload" / "metadata" / "deployment.json").read_text(encoding="utf-8")
    )
    assert str((tmp_path / "source-host-a" / "platform-state").resolve()) not in manifest_text
    assert deployment_metadata["source_environment"] == "source-host-a"

    monkeypatch.setenv("AI_MAP_DATA_DIR", str(restored_root))
    monkeypatch.setenv("AI_MAP_SECURE_COOKIE", "false")
    assert server_main(["recover-restore"]) == 0

    report = json.loads(
        (restored_root / RESTORE_RECOVERY_DIR / RESTORE_RECOVERY_REPORT).read_text(
            encoding="utf-8"
        )
    )
    assert report["ready_for_service"] is True
    assert report["unresolved_run_ids"] == []

    replacement = build_single_node_deployment(
        SingleNodeConfig(data_dir=restored_root, secure_cookie=False)
    )

    async def verify_history() -> None:
        task = await replacement.kernel.get_task(task_id)
        run = await replacement.kernel.get_run(task_id, run_id)
        assert task.task_id == task_id
        assert run.run_id == run_id
        assert run.task_id == task_id
        assert task.status.value == "succeeded"
        assert run.status.value == "succeeded"

    asyncio.run(verify_history())
    assert user_id in replacement.authentication.store.users
    assert replacement.authorization.has_policy(user_id)
