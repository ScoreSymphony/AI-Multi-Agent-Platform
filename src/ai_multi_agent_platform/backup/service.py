"""Offline backup, verification, and deterministic restore for single-node deployments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .recovery import write_restore_recovery_marker

BACKUP_FORMAT_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
_PAYLOAD_DIR = "payload"
_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


class BackupError(RuntimeError):
    """Raised when backup creation, verification, or restore is unsafe."""


@dataclass(frozen=True, slots=True)
class BackupVerification:
    backup_dir: Path
    files_checked: int
    bytes_checked: int
    manifest: dict[str, Any]


def create_single_node_backup(
    *,
    data_dir: Path,
    destination: Path,
    platform_version: str,
    platform_commit: str | None = None,
    deployment_metadata: dict[str, Any] | None = None,
    quiesced: bool = False,
) -> Path:
    """Create an atomic, checksummed backup from a quiesced single-node data root.

    V1 deliberately requires an offline/quiesced source. This makes the database snapshots and
    durable file copies one operator-controlled point-in-time boundary instead of pretending that
    unrelated local providers share a transaction manager.
    """

    source = data_dir.expanduser().resolve()
    target = destination.expanduser().resolve()
    if not quiesced:
        raise BackupError("backup requires an explicitly quiesced deployment")
    if not source.is_dir():
        raise BackupError(f"data directory does not exist: {source}")
    if target.exists():
        raise BackupError(f"backup destination already exists: {target}")
    if _is_within(target, source):
        raise BackupError("backup destination must not be inside the source data directory")

    partial = target.with_name(f".{target.name}.partial")
    if partial.exists():
        shutil.rmtree(partial)
    payload = partial / _PAYLOAD_DIR
    payload.mkdir(parents=True)

    _assert_non_secret_metadata(deployment_metadata or {})

    entries: list[dict[str, Any]] = []
    sqlite_versions: dict[str, int] = {}
    included_components: set[str] = set()
    excluded = [
        {
            "path": "executor/",
            "reason": "ephemeral execution workspace; recreate on the replacement host",
        }
    ]

    try:
        for component in ("db", "files", "workspaces"):
            component_source = source / component
            if not component_source.exists():
                continue
            if not component_source.is_dir():
                raise BackupError(
                    f"expected durable component to be a directory: {component_source}"
                )
            included_components.add(component)
            for item in sorted(component_source.rglob("*")):
                if item.is_symlink():
                    raise BackupError(f"symbolic links are not allowed in backup scope: {item}")
                if item.is_dir():
                    continue
                relative = item.relative_to(source)
                if any(item.name.endswith(suffix) for suffix in _SQLITE_SIDECARS):
                    continue
                backup_path = payload / relative
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                if item.suffix.casefold() in _SQLITE_SUFFIXES:
                    _sqlite_snapshot(item, backup_path)
                    sqlite_versions[relative.as_posix()] = _sqlite_user_version(backup_path)
                else:
                    shutil.copy2(item, backup_path)
                entries.append(_entry_for_file(backup_path, payload))

        metadata_dir = payload / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)
        deployment_file = metadata_dir / "deployment.json"
        deployment_file.write_text(
            json.dumps(deployment_metadata or {"profile": "single-node"}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        entries.append(_entry_for_file(deployment_file, payload))
        included_components.add("configuration-metadata")

        manifest: dict[str, Any] = {
            "backup_format_version": BACKUP_FORMAT_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "platform": {"version": platform_version, "commit": platform_commit},
            "schema_migration": {"sqlite_user_versions": sqlite_versions},
            "consistency": {
                "mode": "offline-quiesced",
                "database_snapshot": "sqlite-backup-api",
                "file_snapshot": "copy-while-quiesced",
            },
            "included_components": sorted(included_components),
            "entries": entries,
            "encryption": {
                "mode": "none",
                "plaintext_secret_material_included": False,
            },
            "external_dependencies": [
                "secret-provider key/material must be recovered separately",
                "optional adapters/providers must be reinstalled or may remain unavailable",
            ],
            "excluded": excluded,
            "restore_policy": {
                "authentication_sessions": "invalidate",
                "workers_nodes": "reauthenticate-and-reregister",
                "stale_leases_reservations": "do-not-resurrect",
                "unfinished_runs": "existing-kernel-reconciliation-required",
                "indexes_caches": "rebuild-not-restore",
            },
        }
        (partial / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        verify_backup(partial)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, target)
        return target
    except Exception:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        raise


def verify_backup(backup_dir: Path) -> BackupVerification:
    root = backup_dir.expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BackupError("backup manifest is missing")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is unreadable or invalid JSON") from exc
    if not isinstance(raw, dict) or raw.get("backup_format_version") != BACKUP_FORMAT_VERSION:
        raise BackupError("incompatible backup format version")
    if raw.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise BackupError("incompatible backup manifest schema version")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise BackupError("backup manifest entries must be an array")

    payload = root / _PAYLOAD_DIR
    seen: set[str] = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("backup manifest contains an invalid entry")
        path = entry.get("path")
        sha256 = entry.get("sha256")
        size = entry.get("size")
        if not isinstance(path, str) or not _safe_relative_posix(path):
            raise BackupError(f"unsafe backup entry path: {path!r}")
        if path in seen:
            raise BackupError(f"duplicate backup entry path: {path}")
        seen.add(path)
        file_path = payload.joinpath(*PurePosixPath(path).parts)
        if not file_path.is_file():
            raise BackupError(f"backup payload file is missing: {path}")
        actual_size = file_path.stat().st_size
        if not isinstance(size, int) or actual_size != size:
            raise BackupError(f"backup payload size mismatch: {path}")
        actual_hash = _sha256(file_path)
        if not isinstance(sha256, str) or actual_hash != sha256:
            raise BackupError(f"backup checksum mismatch: {path}")
        total += actual_size

    actual_files = {
        item.relative_to(payload).as_posix() for item in payload.rglob("*") if item.is_file()
    }
    extras = actual_files - seen
    if extras:
        raise BackupError(f"backup contains unmanifested payload files: {sorted(extras)!r}")
    return BackupVerification(root, len(entries), total, raw)


def restore_single_node_backup(
    *,
    backup_dir: Path,
    target_data_dir: Path,
    expected_platform_version: str | None = None,
) -> Path:
    """Restore into a clean relocatable data root, atomically at directory granularity."""

    verification = verify_backup(backup_dir)
    manifest = verification.manifest
    if expected_platform_version is not None:
        backup_version = manifest.get("platform", {}).get("version")
        if backup_version != expected_platform_version:
            raise BackupError(
                f"incompatible platform version: backup={backup_version!r}, "
                f"expected={expected_platform_version!r}"
            )

    target = target_data_dir.expanduser().resolve()
    if target.exists():
        raise BackupError(f"restore target must not already exist: {target}")
    partial = target.with_name(f".{target.name}.restore-partial")
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)

    try:
        payload = verification.backup_dir / _PAYLOAD_DIR
        for entry in manifest["entries"]:
            relative = PurePosixPath(entry["path"])
            if relative.parts[0] == "metadata":
                continue
            source = payload.joinpath(*relative.parts)
            destination = partial.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        auth_db = partial / "db" / "authentication.sqlite3"
        if auth_db.is_file():
            with sqlite3.connect(auth_db) as connection:
                if _sqlite_table_exists(connection, "auth_sessions"):
                    connection.execute("DELETE FROM auth_sessions")
                    connection.commit()
                _checkpoint_sqlite_wal(connection, auth_db)
            _remove_sqlite_sidecars(auth_db)

        (partial / "executor").mkdir(parents=True, exist_ok=True)
        write_restore_recovery_marker(partial, manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, target)
        return target
    except Exception:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        raise


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                row = dst.execute("PRAGMA integrity_check").fetchone()
                if row is None or row[0] != "ok":
                    raise BackupError(f"SQLite integrity check failed for {source}")
                _checkpoint_sqlite_wal(dst, destination)
        _remove_sqlite_sidecars(destination)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"cannot snapshot SQLite database: {source}") from exc


def _checkpoint_sqlite_wal(connection: sqlite3.Connection, path: Path) -> None:
    mode_row = connection.execute("PRAGMA journal_mode").fetchone()
    mode = str(mode_row[0]).casefold() if mode_row is not None else ""
    if mode != "wal":
        return
    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint is None or int(checkpoint[0]) != 0:
        raise BackupError(f"SQLite WAL checkpoint could not complete for {path}")


def _remove_sqlite_sidecars(path: Path) -> None:
    for suffix in _SQLITE_SIDECARS:
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _sqlite_user_version(path: Path) -> int:
    with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as connection:
        row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def _sqlite_table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _entry_for_file(path: Path, payload_root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(payload_root).as_posix(),
        "size": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_posix(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and ".." not in path.parts
        and "" not in path.parts
    )


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


_SENSITIVE_METADATA_MARKERS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
)


def _assert_non_secret_metadata(value: Any, path: str = "deployment_metadata") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(marker in normalized for marker in _SENSITIVE_METADATA_MARKERS):
                raise BackupError(
                    f"secret-looking metadata is forbidden in generic backups: {path}.{key}"
                )
            _assert_non_secret_metadata(nested, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, nested in enumerate(value):
            _assert_non_secret_metadata(nested, f"{path}[{index}]")
