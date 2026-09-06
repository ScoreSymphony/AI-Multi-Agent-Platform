"""Explicit restart-safe migrations for the durable coordinator SQLite store.

The platform-wide upgrade lifecycle remains owned by :mod:`ai_multi_agent_platform.upgrade`.
This module owns only the coordinator store's local schema/version contract so #41 can inspect
and migrate it without making SQLite or a future workflow engine canonical.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

COORDINATOR_SCHEMA_VERSION = 2
COORDINATOR_MIGRATION_REVISION = "coordination-0002"
LEGACY_COORDINATOR_SCHEMA_VERSION = 1

_REQUIRED_TABLES = frozenset(
    {
        "coordinator_meta",
        "coordinator_plans",
        "coordinator_steps",
        "coordinator_claims",
        "coordinator_fences",
    }
)


class CoordinatorMigrationError(RuntimeError):
    """Raised when coordinator persistence cannot be inspected or migrated safely."""


@dataclass(frozen=True, slots=True)
class CoordinatorStoreMetadata:
    schema_version: int
    migration_revision: str | None

    @property
    def current(self) -> bool:
        return (
            self.schema_version == COORDINATOR_SCHEMA_VERSION
            and self.migration_revision == COORDINATOR_MIGRATION_REVISION
        )


def inspect_coordinator_store(path: str | Path) -> CoordinatorStoreMetadata | None:
    """Inspect version metadata without mutating an absent/uninitialized store."""

    store = Path(path)
    if not store.is_file() or store.stat().st_size == 0:
        return None
    try:
        uri = f"file:{store.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            if not _table_exists(connection, "coordinator_meta"):
                return None
            _validate_required_tables(connection)
            _quick_check(connection)
            schema_row = connection.execute(
                "SELECT value FROM coordinator_meta WHERE key = 'schema_version'"
            ).fetchone()
            if schema_row is None:
                raise CoordinatorMigrationError("coordinator store has no schema_version")
            try:
                schema_version = int(schema_row[0])
            except (TypeError, ValueError) as exc:
                raise CoordinatorMigrationError("invalid coordinator schema_version") from exc
            revision_row = connection.execute(
                "SELECT value FROM coordinator_meta WHERE key = 'migration_revision'"
            ).fetchone()
            revision = None if revision_row is None else str(revision_row[0])
            return CoordinatorStoreMetadata(schema_version, revision)
    except sqlite3.DatabaseError as exc:
        raise CoordinatorMigrationError(f"cannot inspect coordinator store: {exc}") from exc


def coordinator_migration_plan(path: str | Path) -> tuple[str, ...]:
    """Return the deterministic store-local migration plan or fail closed."""

    metadata = inspect_coordinator_store(path)
    if metadata is None or metadata.current:
        return ()
    if metadata.schema_version == LEGACY_COORDINATOR_SCHEMA_VERSION:
        if metadata.migration_revision not in {None, "", "baseline"}:
            raise CoordinatorMigrationError(
                "legacy coordinator store has an unknown migration revision "
                f"{metadata.migration_revision!r}"
            )
        return (COORDINATOR_MIGRATION_REVISION,)
    raise CoordinatorMigrationError(
        f"unsupported coordinator schema {metadata.schema_version}; "
        f"expected {COORDINATOR_SCHEMA_VERSION}"
    )


def migrate_coordinator_store(path: str | Path) -> tuple[str, ...]:
    """Migrate the supported v1 store to v2 atomically and idempotently.

    Canonical Plan/Step payloads, optimistic revisions, Run references, retry/wait/barrier state
    and monotonic fencing counters are preserved byte-for-byte. Ephemeral coordinator claims are
    deliberately invalidated across planned maintenance so stale process ownership is never
    restored after an upgrade. Fencing counters remain, therefore a post-upgrade claim receives a
    strictly newer fence.
    """

    store = Path(path)
    planned = coordinator_migration_plan(store)
    if not planned:
        return ()

    try:
        with sqlite3.connect(store) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            _validate_required_tables(connection)
            _quick_check(connection)

            schema_row = connection.execute(
                "SELECT value FROM coordinator_meta WHERE key = 'schema_version'"
            ).fetchone()
            if schema_row is None or int(schema_row[0]) != LEGACY_COORDINATOR_SCHEMA_VERSION:
                raise CoordinatorMigrationError(
                    "coordinator schema changed after preflight; rerun upgrade preflight"
                )

            # Claims are process ownership, not canonical workflow state. A planned upgrade must
            # reacquire them after restart. Keeping fences prevents pre-upgrade owners/tokens from
            # becoming valid again after the maintenance boundary.
            connection.execute("DELETE FROM coordinator_claims")
            connection.execute(
                "INSERT INTO coordinator_meta(key, value) VALUES('migration_revision', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (COORDINATOR_MIGRATION_REVISION,),
            )
            connection.execute(
                "UPDATE coordinator_meta SET value = ? WHERE key = 'schema_version'",
                (str(COORDINATOR_SCHEMA_VERSION),),
            )
            _quick_check(connection)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        raise CoordinatorMigrationError(f"coordinator migration failed: {exc}") from exc

    metadata = inspect_coordinator_store(store)
    if metadata is None or not metadata.current:
        raise CoordinatorMigrationError("coordinator migration did not reach the expected revision")
    return planned


def _validate_required_tables(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = sorted(_REQUIRED_TABLES - tables)
    if missing:
        raise CoordinatorMigrationError(
            "coordinator store is missing required tables: " + ", ".join(missing)
        )


def _quick_check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise CoordinatorMigrationError(f"coordinator SQLite quick_check failed: {result!r}")


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )
