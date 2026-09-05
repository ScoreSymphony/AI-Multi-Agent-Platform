"""Offline backup, verification, and deterministic restore for single-node deployments."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .dependencies import DependencyInventoryError, discover_single_node_external_dependencies
from .inventory import required_single_node_store_paths
from .manifest import ManifestSchemaError, validate_backup_manifest_v1
from .recovery import write_restore_recovery_marker

BACKUP_FORMAT_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
_PAYLOAD_DIR = "payload"
_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
_REQUIRED_SINGLE_NODE_COMPONENTS = frozenset(
    {"db", "files", "workspaces", "configuration-metadata"}
)
_REQUIRED_SINGLE_NODE_ENTRIES = frozenset(
    (*required_single_node_store_paths(), "metadata/deployment.json")
)


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
    _validate_single_node_layout(source, context="backup source")
    if target.exists():
        raise BackupError(f"backup destination already exists: {target}")
    if _is_within(target, source):
        raise BackupError("backup destination must not be inside the source data directory")

    metadata: dict[str, Any] = {"profile": "single-node"}
    if deployment_metadata is not None:
        supplied_profile = deployment_metadata.get("profile")
        if supplied_profile is not None and supplied_profile != "single-node":
            raise BackupError("single-node backup metadata profile must be 'single-node'")
        metadata.update(deployment_metadata)
    _assert_non_secret_metadata(metadata)
    try:
        external_dependencies = discover_single_node_external_dependencies(source, metadata)
    except DependencyInventoryError as exc:
        raise BackupError(f"cannot inventory external dependencies: {exc}") from exc

    partial = target.with_name(f".{target.name}.partial")
    if partial.exists():
        shutil.rmtree(partial)
    payload = partial / _PAYLOAD_DIR
    payload.mkdir(parents=True)

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
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
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
            "external_dependencies": [item.to_manifest() for item in external_dependencies],
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
    """Validate schema, scope completeness, checksums, and SQLite payload integrity."""

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
    try:
        manifest = validate_backup_manifest_v1(raw)
    except ManifestSchemaError as exc:
        raise BackupError(f"backup manifest schema validation failed: {exc}") from exc

    entries = manifest["entries"]
    if not isinstance(entries, list):  # guarded by JSON Schema; retained for type/runtime defense
        raise BackupError("backup manifest entries must be an array")
    sqlite_versions = _manifest_sqlite_versions(manifest)

    payload = root / _PAYLOAD_DIR
    if not payload.is_dir():
        raise BackupError("backup payload directory is missing")
    seen: set[str] = set()
    sqlite_paths: set[str] = set()
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
        if _is_sqlite_path(path):
            sqlite_paths.add(path)
            _verify_sqlite_integrity(file_path)
            declared_version = sqlite_versions.get(path)
            if declared_version is None:
                raise BackupError(f"SQLite schema version metadata is missing: {path}")
            actual_version = _sqlite_user_version(file_path)
            if actual_version != declared_version:
                raise BackupError(
                    f"SQLite schema version mismatch: {path} "
                    f"manifest={declared_version} actual={actual_version}"
                )
        total += actual_size

    declared_sqlite_paths = set(sqlite_versions)
    if declared_sqlite_paths != sqlite_paths:
        missing_payload = declared_sqlite_paths - sqlite_paths
        missing_metadata = sqlite_paths - declared_sqlite_paths
        raise BackupError(
            "SQLite schema metadata does not match backup payload: "
            f"missing_payload={sorted(missing_payload)!r} "
            f"missing_metadata={sorted(missing_metadata)!r}"
        )

    actual_files = {
        item.relative_to(payload).as_posix() for item in payload.rglob("*") if item.is_file()
    }
    extras = actual_files - seen
    if extras:
        raise BackupError(f"backup contains unmanifested payload files: {sorted(extras)!r}")
    _verify_required_backup_scope(manifest=manifest, payload=payload, seen=seen)
    return BackupVerification(root, len(entries), total, manifest)


def restore_single_node_backup(
    *,
    backup_dir: Path,
    target_data_dir: Path,
    expected_platform_version: str | None = None,
    expected_platform_commit: str | None = None,
) -> Path:
    """Restore into a clean relocatable data root, atomically at directory granularity."""

    verification = verify_backup(backup_dir)
    manifest = verification.manifest
    platform = manifest.get("platform")
    if not isinstance(platform, dict):  # guarded by schema
        raise BackupError("backup platform metadata is invalid")
    if expected_platform_version is not None:
        backup_version = platform.get("version")
        if backup_version != expected_platform_version:
            raise BackupError(
                f"incompatible platform version: backup={backup_version!r}, "
                f"expected={expected_platform_version!r}"
            )
    if expected_platform_commit is not None:
        backup_commit = platform.get("commit")
        if backup_commit != expected_platform_commit:
            raise BackupError(
                f"incompatible platform commit: backup={backup_commit!r}, "
                f"expected={expected_platform_commit!r}"
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
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise BackupError("validated backup entry unexpectedly became invalid")
            relative = PurePosixPath(entry["path"])
            if relative.parts[0] == "metadata":
                continue
            source = payload.joinpath(*relative.parts)
            destination = partial.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        # Directory-only durable components have no file entry in the manifest. Materialize the
        # required deployment scope explicitly so an empty files/ or workspaces/ component survives
        # the round trip without weakening source-side completeness validation.
        for component in ("db", "files", "workspaces"):
            (partial / component).mkdir(parents=True, exist_ok=True)

        auth_db = partial / "db" / "authentication.sqlite3"
        if auth_db.is_file():
            with sqlite3.connect(auth_db) as connection:
                if _sqlite_table_exists(connection, "auth_sessions"):
                    connection.execute("DELETE FROM auth_sessions")
                    connection.commit()
                _checkpoint_sqlite_wal(connection, auth_db)
            _remove_sqlite_sidecars(auth_db)

        (partial / "executor").mkdir(parents=True, exist_ok=True)
        verify_restored_single_node_data_root(
            partial,
            expected_sqlite_user_versions=_manifest_sqlite_versions(manifest),
        )
        write_restore_recovery_marker(partial, manifest)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(partial, target)
        return target
    except Exception:
        if partial.exists():
            shutil.rmtree(partial, ignore_errors=True)
        raise


def verify_restored_single_node_data_root(
    data_dir: Path,
    *,
    expected_sqlite_user_versions: Mapping[str, int],
) -> tuple[str, ...]:
    """Verify restored durable layout and SQLite schema/integrity before serving."""

    root = data_dir.expanduser().resolve()
    _validate_single_node_layout(root, context="restored data root")

    actual_versions: dict[str, int] = {}
    database_dir = root / "db"
    for item in sorted(database_dir.rglob("*")):
        if item.is_symlink():
            raise BackupError(f"symbolic link found in restored database scope: {item}")
        if not item.is_file() or item.suffix.casefold() not in _SQLITE_SUFFIXES:
            continue
        relative = item.relative_to(root).as_posix()
        _verify_sqlite_integrity(item)
        actual_versions[relative] = _sqlite_user_version(item)

    expected = dict(expected_sqlite_user_versions)
    if set(actual_versions) != set(expected):
        raise BackupError(
            "restored SQLite file set differs from backup manifest: "
            f"expected={sorted(expected)!r} actual={sorted(actual_versions)!r}"
        )
    for path, version in expected.items():
        actual = actual_versions[path]
        if actual != version:
            raise BackupError(
                f"restored SQLite schema version mismatch: {path} "
                f"expected={version} actual={actual}"
            )
    return tuple(sorted(actual_versions))


def _validate_single_node_layout(root: Path, *, context: str) -> None:
    if not root.is_dir():
        raise BackupError(f"{context} does not exist or is not a directory: {root}")
    for component in ("db", "files", "workspaces"):
        path = root / component
        if path.is_symlink():
            raise BackupError(f"{context} durable component must not be a symbolic link: {path}")
        if not path.is_dir():
            raise BackupError(f"{context} is missing required durable component: {component}/")
    for relative in required_single_node_store_paths():
        store = root.joinpath(*PurePosixPath(relative).parts)
        if store.is_symlink():
            raise BackupError(f"{context} durable store must not be a symbolic link: {relative}")
        if not store.is_file():
            raise BackupError(f"{context} is missing required durable store: {relative}")


def _verify_required_backup_scope(
    *,
    manifest: dict[str, Any],
    payload: Path,
    seen: set[str],
) -> None:
    included = manifest.get("included_components")
    if not isinstance(included, list):
        raise BackupError("backup included_components metadata is invalid")
    included_set = {item for item in included if isinstance(item, str)}
    missing_components = _REQUIRED_SINGLE_NODE_COMPONENTS - included_set
    if missing_components:
        raise BackupError(
            f"backup is missing required single-node components: {sorted(missing_components)!r}"
        )
    missing_entries = _REQUIRED_SINGLE_NODE_ENTRIES - seen
    if missing_entries:
        raise BackupError(
            f"backup is missing required single-node entries: {sorted(missing_entries)!r}"
        )

    metadata_path = payload / "metadata" / "deployment.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup deployment metadata is unreadable or invalid JSON") from exc
    if not isinstance(metadata, dict) or metadata.get("profile") != "single-node":
        raise BackupError("backup deployment metadata does not identify the single-node profile")
    _assert_non_secret_metadata(metadata)


def _manifest_sqlite_versions(manifest: dict[str, Any]) -> dict[str, int]:
    schema_migration = manifest.get("schema_migration")
    if not isinstance(schema_migration, dict):
        raise BackupError("backup schema_migration metadata is invalid")
    raw_versions = schema_migration.get("sqlite_user_versions")
    if not isinstance(raw_versions, dict):
        raise BackupError("backup sqlite_user_versions metadata is invalid")
    versions: dict[str, int] = {}
    for path, version in raw_versions.items():
        if not isinstance(path, str) or not isinstance(version, int):
            raise BackupError("backup sqlite_user_versions contains invalid entries")
        if not _safe_relative_posix(path) or not _is_sqlite_path(path):
            raise BackupError(f"invalid SQLite schema metadata path: {path!r}")
        versions[path] = version
    return versions


def _sqlite_snapshot(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                row = dst.execute("PRAGMA integrity_check").fetchone()
                if row is None or row[0] != "ok":
                    raise BackupError(f"SQLite integrity check failed for {source}")
                foreign_key_violation = dst.execute("PRAGMA foreign_key_check").fetchone()
                if foreign_key_violation is not None:
                    raise BackupError(f"SQLite foreign-key check failed for {source}")
                _checkpoint_sqlite_wal(dst, destination)
        _remove_sqlite_sidecars(destination)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"cannot snapshot SQLite database: {source}") from exc


def _verify_sqlite_integrity(path: Path) -> None:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as connection:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_key_violation = connection.execute("PRAGMA foreign_key_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite database cannot be verified: {path}") from exc
    if row is None or row[0] != "ok":
        raise BackupError(f"SQLite integrity check failed: {path}")
    if foreign_key_violation is not None:
        raise BackupError(f"SQLite foreign-key check failed: {path}")


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
    try:
        with sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True) as connection:
            row = connection.execute("PRAGMA user_version").fetchone()
    except sqlite3.Error as exc:
        raise BackupError(f"cannot read SQLite schema version: {path}") from exc
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


def _is_sqlite_path(value: str) -> bool:
    return PurePosixPath(value).suffix.casefold() in _SQLITE_SUFFIXES


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
