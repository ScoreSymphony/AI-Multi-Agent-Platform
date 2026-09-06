"""Canonical inventory of durable state required for single-node portability."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DurableStoreSpec:
    name: str
    relative_path: str
    kind: str
    required: bool
    domain: str


def _upgrade_store_specs() -> tuple[DurableStoreSpec, ...]:
    # Imported lazily to keep the backup/inventory layer independent from runtime
    # migration tooling while still sharing one canonical upgrade durability contract.
    from ai_multi_agent_platform.upgrade.versioning import (
        CURRENT_VERSION_FILE,
        MIGRATION_HISTORY_FILE,
    )

    return (
        DurableStoreSpec(
            "upgrade-version",
            CURRENT_VERSION_FILE,
            "json-file",
            True,
            "upgrade",
        ),
        DurableStoreSpec(
            "upgrade-migrations",
            MIGRATION_HISTORY_FILE,
            "json-file",
            True,
            "upgrade",
        ),
    )


_BASE_DURABLE_STORES: tuple[DurableStoreSpec, ...] = (
    DurableStoreSpec("kernel", "db/kernel.sqlite3", "sqlite", True, "kernel"),
    DurableStoreSpec("authority", "db/authority.sqlite3", "sqlite", True, "authority"),
    DurableStoreSpec("connectors", "db/connectors.sqlite3", "sqlite", True, "connectors"),
    DurableStoreSpec("files", "files", "directory", True, "files"),
    DurableStoreSpec(
        "run-workspace-bindings",
        "db/run-workspace-bindings.sqlite3",
        "sqlite",
        True,
        "workspaces",
    ),
    DurableStoreSpec(
        "repository-bindings",
        "db/repository-bindings.sqlite3",
        "sqlite",
        True,
        "repositories",
    ),
    DurableStoreSpec(
        "repository-provenance",
        "db/repository-provenance.sqlite3",
        "sqlite",
        True,
        "repositories",
    ),
    DurableStoreSpec("verification", "db/verification.sqlite3", "sqlite", True, "verification"),
    DurableStoreSpec("evaluation", "db/evaluation.sqlite3", "sqlite", True, "evaluation"),
    DurableStoreSpec("authentication", "db/authentication.sqlite3", "sqlite", True, "security"),
)


DURABLE_STORES: tuple[DurableStoreSpec, ...] = _BASE_DURABLE_STORES + _upgrade_store_specs()
