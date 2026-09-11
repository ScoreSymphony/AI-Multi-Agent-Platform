#!/usr/bin/env python3
from __future__ import annotations

from importlib.resources import files

from ai_multi_agent_platform.backup.manifest import (
    ManifestSchemaError,
    backup_manifest_v1_schema,
    validate_backup_manifest_v1,
)
from ai_multi_agent_platform.release.discovery import load_compatibility_inventory


def main() -> int:
    backup_resource = files("ai_multi_agent_platform.backup").joinpath(
        "backup-manifest-v1.schema.json"
    )
    release_resource = files("ai_multi_agent_platform.release").joinpath(
        "compatibility.json"
    )

    if not backup_resource.is_file():
        raise RuntimeError("installed backup manifest schema resource is missing")
    if not release_resource.is_file():
        raise RuntimeError("installed release compatibility resource is missing")

    schema = backup_manifest_v1_schema()
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise RuntimeError("installed backup manifest schema did not load as Draft 2020-12")

    valid_manifest = {
        "backup_format_version": 1,
        "manifest_schema_version": 1,
        "created_at": "2026-09-11T00:00:00Z",
        "platform": {"version": "0.0.1", "commit": None},
        "schema_migration": {"sqlite_user_versions": {}},
        "consistency": {
            "mode": "offline-quiesced",
            "database_snapshot": "sqlite-backup-api",
            "file_snapshot": "copy-while-quiesced",
        },
        "included_components": [],
        "entries": [],
        "encryption": {
            "mode": "none",
            "plaintext_secret_material_included": False,
        },
        "external_dependencies": [],
        "excluded": [],
        "restore_policy": {
            "authentication_sessions": "invalidate",
            "workers_nodes": "reauthenticate-and-reregister",
            "stale_leases_reservations": "do-not-resurrect",
            "unfinished_runs": "existing-kernel-reconciliation-required",
            "indexes_caches": "rebuild-not-restore",
        },
    }
    if validate_backup_manifest_v1(valid_manifest) != valid_manifest:
        raise RuntimeError("installed backup manifest validation changed the manifest")

    try:
        validate_backup_manifest_v1({})
    except ManifestSchemaError:
        pass
    else:
        raise RuntimeError("installed backup manifest validator accepted an invalid manifest")

    inventory = load_compatibility_inventory()
    if inventory.schema_version != "2":
        raise RuntimeError("installed compatibility inventory has an unexpected schema version")
    if inventory.platform_release != inventory.versions.platform_release:
        raise RuntimeError("installed compatibility inventory version snapshot is inconsistent")
    if not inventory.entries:
        raise RuntimeError("installed compatibility inventory contains no upstream entries")

    print("Installed wheel runtime resources load and validate successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
