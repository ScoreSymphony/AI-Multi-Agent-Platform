from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from ai_multi_agent_platform.backup import (
    BackupError,
    create_single_node_backup,
    restore_single_node_backup,
    verify_backup,
)
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.distributed import (
    DistributedRegistry,
    DistributedRuntime,
    JsonDistributedStateStore,
)

_DISTRIBUTED_STATE_PATH = "db/distributed-runtime-state.json"


def _source_with_distributed_state(root: Path) -> Path:
    build_single_node_deployment(SingleNodeConfig(data_dir=root, secure_cookie=False))
    registry = DistributedRegistry()
    runtime = DistributedRuntime(registry)
    JsonDistributedStateStore(root / _DISTRIBUTED_STATE_PATH).save(registry, runtime)
    return root


def _manifest(backup: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((backup / "manifest.json").read_text(encoding="utf-8")),
    )


def test_distributed_runtime_state_round_trips_through_single_node_backup(tmp_path: Path) -> None:
    source = _source_with_distributed_state(tmp_path / "source")
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )

    verification = verify_backup(backup)
    assert "distributed-runtime-state" in verification.manifest["included_components"]
    assert any(
        entry["path"] == _DISTRIBUTED_STATE_PATH
        for entry in verification.manifest["entries"]
    )

    restored = restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / "restored",
        expected_platform_version="0.0.1",
    )
    restored_state = restored / _DISTRIBUTED_STATE_PATH
    assert restored_state.is_file()

    registry = DistributedRegistry()
    runtime = DistributedRuntime(registry)
    assert JsonDistributedStateStore(restored_state).restore(registry, runtime) is True


def test_distributed_backup_without_runtime_state_is_rejected(tmp_path: Path) -> None:
    source = _source_with_distributed_state(tmp_path / "source")
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )

    manifest = _manifest(backup)
    manifest["entries"] = [
        entry for entry in manifest["entries"] if entry["path"] != _DISTRIBUTED_STATE_PATH
    ]
    (backup / "payload" / _DISTRIBUTED_STATE_PATH).unlink()
    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(BackupError, match="missing required distributed runtime state"):
        verify_backup(backup)
