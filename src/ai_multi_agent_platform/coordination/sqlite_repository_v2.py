"""Current durable SQLite coordinator repository schema.

The v1 implementation remains importable only as the historical migration fixture. Public platform
composition uses this class and therefore refuses to mutate an older store implicitly at startup.
"""

from __future__ import annotations

from .migrations import COORDINATOR_MIGRATION_REVISION, COORDINATOR_SCHEMA_VERSION
from .sqlite_repository import SQLiteCoordinatorRepository as _V1SQLiteCoordinatorRepository


class SQLiteCoordinatorRepository(_V1SQLiteCoordinatorRepository):
    """Coordinator repository using the current explicit store migration contract."""

    def _initialize(self) -> None:
        with self._connect() as connection:
            meta_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'coordinator_meta'"
            ).fetchone()
            if meta_exists is not None:
                schema_row = connection.execute(
                    "SELECT value FROM coordinator_meta WHERE key = 'schema_version'"
                ).fetchone()
                revision_row = connection.execute(
                    "SELECT value FROM coordinator_meta WHERE key = 'migration_revision'"
                ).fetchone()
                if (
                    schema_row is None
                    or int(schema_row[0]) != COORDINATOR_SCHEMA_VERSION
                    or revision_row is None
                    or str(revision_row[0]) != COORDINATOR_MIGRATION_REVISION
                ):
                    found = "missing" if schema_row is None else str(schema_row[0])
                    raise RuntimeError(
                        "coordinator persistence requires an explicit platform upgrade: "
                        f"found schema {found}, expected {COORDINATOR_SCHEMA_VERSION} "
                        f"at {COORDINATOR_MIGRATION_REVISION}"
                    )
                return

            connection.executescript(
                """
                CREATE TABLE coordinator_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE coordinator_plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    store_revision INTEGER NOT NULL
                );
                CREATE TABLE coordinator_steps (
                    step_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES coordinator_plans(plan_id) ON DELETE CASCADE
                );
                CREATE TABLE coordinator_claims (
                    step_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    fence INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(step_id) REFERENCES coordinator_steps(step_id) ON DELETE CASCADE
                );
                CREATE TABLE coordinator_fences (
                    step_id TEXT PRIMARY KEY,
                    fence INTEGER NOT NULL
                );
                """
            )
            connection.executemany(
                "INSERT INTO coordinator_meta(key, value) VALUES(?, ?)",
                (
                    ("schema_version", str(COORDINATOR_SCHEMA_VERSION)),
                    ("migration_revision", COORDINATOR_MIGRATION_REVISION),
                ),
            )
