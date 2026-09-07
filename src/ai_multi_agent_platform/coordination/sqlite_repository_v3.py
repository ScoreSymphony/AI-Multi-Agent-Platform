"""Current durable SQLite coordinator repository schema v3."""

from __future__ import annotations

from datetime import datetime

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .migrations import COORDINATOR_MIGRATION_REVISION, COORDINATOR_SCHEMA_VERSION
from .models import PlanRuntimeState
from .retirement import PlanRetirement
from .sqlite_repository_v2 import SQLiteCoordinatorRepository as _V2SQLiteCoordinatorRepository


class SQLiteCoordinatorRepository(_V2SQLiteCoordinatorRepository):
    """Coordinator repository with durable superseded-Plan retirement state."""

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
                retirement_table = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'coordinator_plan_retirements'"
                ).fetchone()
                if (
                    schema_row is None
                    or int(schema_row[0]) != COORDINATOR_SCHEMA_VERSION
                    or revision_row is None
                    or str(revision_row[0]) != COORDINATOR_MIGRATION_REVISION
                    or retirement_table is None
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
                CREATE TABLE coordinator_plan_retirements (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    superseded_by_plan_id TEXT,
                    reason TEXT NOT NULL,
                    retired_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES coordinator_plans(plan_id) ON DELETE CASCADE
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

    def retire_plan(
        self,
        plan_id: str,
        *,
        superseded_by_plan_id: str | None,
        reason: str,
        retired_at: datetime,
    ) -> PlanRetirement:
        state = self.get_plan(plan_id)
        candidate = PlanRetirement(
            plan_id=plan_id,
            task_id=state.plan.task_id,
            superseded_by_plan_id=superseded_by_plan_id,
            reason=reason,
            retired_at=retired_at,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT task_id, superseded_by_plan_id, reason, retired_at "
                "FROM coordinator_plan_retirements WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is not None:
                existing = _retirement_from_row(plan_id, row)
                if (
                    existing.superseded_by_plan_id == candidate.superseded_by_plan_id
                    and existing.reason == candidate.reason
                ):
                    return existing
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"coordination Plan {plan_id} already has a different retirement",
                )
            connection.execute(
                "INSERT INTO coordinator_plan_retirements("
                "plan_id, task_id, superseded_by_plan_id, reason, retired_at"
                ") VALUES(?, ?, ?, ?, ?)",
                (
                    candidate.plan_id,
                    candidate.task_id,
                    candidate.superseded_by_plan_id,
                    candidate.reason,
                    candidate.retired_at.isoformat(),
                ),
            )
            return candidate

    def plan_retirement(self, plan_id: str) -> PlanRetirement | None:
        self.get_plan(plan_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT task_id, superseded_by_plan_id, reason, retired_at "
                "FROM coordinator_plan_retirements WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return None if row is None else _retirement_from_row(plan_id, row)

    def list_active_plans(self) -> tuple[PlanRuntimeState, ...]:
        with self._connect() as connection:
            ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT p.plan_id FROM coordinator_plans AS p "
                    "LEFT JOIN coordinator_plan_retirements AS r ON r.plan_id = p.plan_id "
                    "WHERE r.plan_id IS NULL ORDER BY p.plan_id"
                ).fetchall()
            )
        return tuple(self.get_plan(plan_id) for plan_id in ids)


def _retirement_from_row(plan_id: str, row: tuple[object, ...]) -> PlanRetirement:
    return PlanRetirement(
        plan_id=plan_id,
        task_id=str(row[0]),
        superseded_by_plan_id=None if row[1] is None else str(row[1]),
        reason=str(row[2]),
        retired_at=datetime.fromisoformat(str(row[3])),
    )


__all__ = ["SQLiteCoordinatorRepository"]
