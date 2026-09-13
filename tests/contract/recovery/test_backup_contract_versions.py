from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.backup import (
    BackupError,
    create_single_node_backup,
    restore_single_node_backup,
    verify_backup,
)
from ai_multi_agent_platform.backup.inventory import (
    SINGLE_NODE_STORE_CONTRACT_VERSION,
    required_single_node_store_paths,
)
from ai_multi_agent_platform.upgrade.preflight import _backup_checks

_LEARNING_STORES = {
    "db/learning.sqlite3",
    "db/learning-post-promotion.sqlite3",
}


def test_pre_learning_backup_is_accepted_by_upgrade_verification(tmp_path: Path) -> None:
    source = _current_data_root(tmp_path / "source")
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )
    _downgrade_to_pre_learning_backup(backup)

    verification = verify_backup(backup)
    assert verification.manifest.get("store_contract_version") is None
    checks = _backup_checks(
        backup,
        current=cast(Any, SimpleNamespace(platform_release="0.0.1")),
        required=True,
        verifier=verify_backup,
    )
    assert tuple(check.code for check in checks) == ("backup.verified",)


def test_current_backup_without_learning_store_is_rejected(tmp_path: Path) -> None:
    source = _current_data_root(tmp_path / "source")
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )
    manifest = _manifest(backup)
    assert manifest["store_contract_version"] == SINGLE_NODE_STORE_CONTRACT_VERSION
    _remove_learning_entries(backup, manifest)

    with pytest.raises(BackupError, match="required single-node entries"):
        verify_backup(backup)


def test_restoring_pre_learning_backup_initializes_new_required_stores(tmp_path: Path) -> None:
    source = _current_data_root(tmp_path / "source")
    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )
    _downgrade_to_pre_learning_backup(backup)

    restored = restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / "restored",
        expected_platform_version="0.0.1",
    )

    for relative in sorted(_LEARNING_STORES):
        path = restored / relative
        assert path.is_file()
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA user_version").fetchone() == (0,)


def test_restore_preserves_existing_learning_data(tmp_path: Path) -> None:
    source = _current_data_root(tmp_path / "source")
    learning_db = source / "db" / "learning.sqlite3"
    with sqlite3.connect(learning_db) as connection:
        connection.execute("CREATE TABLE learning_marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO learning_marker(value) VALUES ('preserve-me')")

    backup = create_single_node_backup(
        data_dir=source,
        destination=tmp_path / "backup",
        platform_version="0.0.1",
        quiesced=True,
    )
    restored = restore_single_node_backup(
        backup_dir=backup,
        target_data_dir=tmp_path / "restored",
        expected_platform_version="0.0.1",
    )

    with sqlite3.connect(restored / "db" / "learning.sqlite3") as connection:
        assert connection.execute("SELECT value FROM learning_marker").fetchone() == (
            "preserve-me",
        )


def test_required_store_inventory_is_contract_version_aware() -> None:
    historical = set(required_single_node_store_paths(store_contract_version=1))
    current = set(required_single_node_store_paths())

    assert historical.isdisjoint(_LEARNING_STORES)
    assert _LEARNING_STORES <= current


def _current_data_root(root: Path) -> Path:
    (root / "db").mkdir(parents=True)
    (root / "files").mkdir()
    (root / "workspaces").mkdir()
    for relative in required_single_node_store_paths():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        assert path.suffix in {".sqlite", ".sqlite3", ".db"}
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS backup_fixture(id INTEGER PRIMARY KEY)")
    return root


def _manifest(backup: Path) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((backup / "manifest.json").read_text(encoding="utf-8")),
    )


def _downgrade_to_pre_learning_backup(backup: Path) -> None:
    manifest = _manifest(backup)
    manifest.pop("store_contract_version", None)
    _remove_learning_entries(backup, manifest)


def _remove_learning_entries(backup: Path, manifest: dict[str, Any]) -> None:
    manifest["entries"] = [
        entry for entry in manifest["entries"] if entry["path"] not in _LEARNING_STORES
    ]
    versions = manifest["schema_migration"]["sqlite_user_versions"]
    for relative in _LEARNING_STORES:
        versions.pop(relative, None)
        path = backup / "payload" / relative
        if path.exists():
            path.unlink()
    (backup / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
