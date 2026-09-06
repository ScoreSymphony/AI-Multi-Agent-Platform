"""Durable single-node SQLite repository for issue #384 coordination state."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, Plan, Provenance, Step, StepStatus

from .models import (
    CoordinationPhase,
    CoordinatorClaim,
    PlanRuntimeState,
    PredecessorFailurePolicy,
    ReconciliationDisposition,
    StepCoordinationRecord,
    StepRetryPolicy,
    StepWait,
    WaitResolution,
    WaitType,
)

_SCHEMA_VERSION = 1


def _dt(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _dump(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, Any]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("persisted coordinator JSON must be an object")
    return cast(dict[str, Any], loaded)


def _owner_to_dict(owner: OwnerRef) -> dict[str, str]:
    return {"type": owner.type, "id": owner.id}


def _owner_from_dict(value: dict[str, Any]) -> OwnerRef:
    owner_type = str(value["type"])
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("invalid persisted owner type")
    return OwnerRef(type=cast(Any, owner_type), id=str(value["id"]))


def _provenance_to_dict(value: Provenance | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"source": value.source, "actor_ref": value.actor_ref, "details": dict(value.details)}


def _provenance_from_dict(value: dict[str, Any] | None) -> Provenance | None:
    if value is None:
        return None
    return Provenance(
        source=str(value["source"]),
        actor_ref=cast(str | None, value.get("actor_ref")),
        details=cast(dict[str, Any], value.get("details", {})),
    )


def _plan_to_dict(plan: Plan) -> dict[str, Any]:
    return {
        "id": plan.id,
        "task_id": plan.task_id,
        "owner_ref": _owner_to_dict(plan.owner_ref),
        "revision": plan.revision,
        "active": plan.active,
        "project_id": plan.project_id,
        "created_at": plan.created_at.isoformat(),
        "schema_version": plan.schema_version,
        "provenance": _provenance_to_dict(plan.provenance),
        "external_refs": [asdict(item) for item in plan.external_refs],
    }


def _plan_from_dict(value: dict[str, Any]) -> Plan:
    from ai_multi_agent_platform.domain import ExternalRef

    return Plan(
        id=str(value["id"]),
        task_id=str(value["task_id"]),
        owner_ref=_owner_from_dict(cast(dict[str, Any], value["owner_ref"])),
        revision=int(value["revision"]),
        active=bool(value["active"]),
        project_id=cast(str | None, value.get("project_id")),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        schema_version=str(value["schema_version"]),
        provenance=_provenance_from_dict(cast(dict[str, Any] | None, value.get("provenance"))),
        external_refs=tuple(
            ExternalRef(
                system=str(item["system"]), kind=str(item["kind"]), value=str(item["value"])
            )
            for item in cast(list[dict[str, Any]], value.get("external_refs", []))
        ),
    )


def _step_to_dict(step: Step) -> dict[str, Any]:
    return {
        "id": step.id,
        "plan_id": step.plan_id,
        "title": step.title,
        "owner_ref": _owner_to_dict(step.owner_ref),
        "status": step.status.value,
        "parent_step_id": step.parent_step_id,
        "depends_on": list(step.depends_on),
        "project_id": step.project_id,
        "created_at": step.created_at.isoformat(),
        "updated_at": step.updated_at.isoformat(),
        "schema_version": step.schema_version,
        "provenance": _provenance_to_dict(step.provenance),
        "external_refs": [asdict(item) for item in step.external_refs],
    }


def _step_from_dict(value: dict[str, Any]) -> Step:
    from ai_multi_agent_platform.domain import ExternalRef

    return Step(
        id=str(value["id"]),
        plan_id=str(value["plan_id"]),
        title=str(value["title"]),
        owner_ref=_owner_from_dict(cast(dict[str, Any], value["owner_ref"])),
        status=StepStatus(str(value["status"])),
        parent_step_id=cast(str | None, value.get("parent_step_id")),
        depends_on=tuple(cast(list[str], value.get("depends_on", []))),
        project_id=cast(str | None, value.get("project_id")),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
        schema_version=str(value["schema_version"]),
        provenance=_provenance_from_dict(cast(dict[str, Any] | None, value.get("provenance"))),
        external_refs=tuple(
            ExternalRef(
                system=str(item["system"]), kind=str(item["kind"]), value=str(item["value"])
            )
            for item in cast(list[dict[str, Any]], value.get("external_refs", []))
        ),
    )


def _wait_to_dict(wait: StepWait | None) -> dict[str, Any] | None:
    if wait is None:
        return None
    return {
        "wait_key": wait.wait_key,
        "wait_type": wait.wait_type.value,
        "task_id": wait.task_id,
        "plan_id": wait.plan_id,
        "step_id": wait.step_id,
        "owner_ref": _owner_to_dict(wait.owner_ref),
        "project_id": wait.project_id,
        "deadline_at": wait.deadline_at.isoformat() if wait.deadline_at else None,
        "approval_id": wait.approval_id,
        "approval_subject_type": wait.approval_subject_type,
        "approval_subject_id": wait.approval_subject_id,
        "approval_action": wait.approval_action,
        "event_type": wait.event_type,
        "correlation_key": wait.correlation_key,
        "external_job_ref": wait.external_job_ref,
        "created_at": wait.created_at.isoformat(),
        "resolved_at": wait.resolved_at.isoformat() if wait.resolved_at else None,
        "resolution": wait.resolution.value if wait.resolution else None,
        "resolution_key": wait.resolution_key,
    }


def _wait_from_dict(value: dict[str, Any] | None) -> StepWait | None:
    if value is None:
        return None
    resolution = value.get("resolution")
    return StepWait(
        wait_key=str(value["wait_key"]),
        wait_type=WaitType(str(value["wait_type"])),
        task_id=str(value["task_id"]),
        plan_id=str(value["plan_id"]),
        step_id=str(value["step_id"]),
        owner_ref=_owner_from_dict(cast(dict[str, Any], value["owner_ref"])),
        project_id=cast(str | None, value.get("project_id")),
        deadline_at=_dt(cast(str | None, value.get("deadline_at"))),
        approval_id=cast(str | None, value.get("approval_id")),
        approval_subject_type=cast(str | None, value.get("approval_subject_type")),
        approval_subject_id=cast(str | None, value.get("approval_subject_id")),
        approval_action=cast(str | None, value.get("approval_action")),
        event_type=cast(str | None, value.get("event_type")),
        correlation_key=cast(str | None, value.get("correlation_key")),
        external_job_ref=cast(str | None, value.get("external_job_ref")),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        resolved_at=_dt(cast(str | None, value.get("resolved_at"))),
        resolution=WaitResolution(str(resolution)) if resolution is not None else None,
        resolution_key=cast(str | None, value.get("resolution_key")),
    )


def _record_to_dict(record: StepCoordinationRecord) -> dict[str, Any]:
    retry = record.retry_policy
    return {
        "task_id": record.task_id,
        "plan_id": record.plan_id,
        "plan_revision": record.plan_revision,
        "step_id": record.step_id,
        "phase": record.phase.value,
        "dependency_ids": list(record.dependency_ids),
        "satisfied_dependency_ids": list(record.satisfied_dependency_ids),
        "latest_run_id": record.latest_run_id,
        "current_attempt": record.current_attempt,
        "retry_policy": {
            "max_attempts": retry.max_attempts,
            "initial_delay_seconds": retry.initial_delay_seconds,
            "multiplier": retry.multiplier,
            "max_delay_seconds": retry.max_delay_seconds,
            "retryable_categories": list(retry.retryable_categories),
            "version": retry.version,
        },
        "retry_due_at": record.retry_due_at.isoformat() if record.retry_due_at else None,
        "wait": _wait_to_dict(record.wait),
        "predecessor_failure_policy": record.predecessor_failure_policy.value,
        "processed_keys": list(record.processed_keys),
        "reconciliation": record.reconciliation.value,
        "reconciliation_detail": record.reconciliation_detail,
        "correlation_id": record.correlation_id,
        "causation_id": record.causation_id,
        "provenance_source": record.provenance_source,
        "revision": record.revision,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_dict(value: dict[str, Any]) -> StepCoordinationRecord:
    retry = cast(dict[str, Any], value["retry_policy"])
    return StepCoordinationRecord(
        task_id=str(value["task_id"]),
        plan_id=str(value["plan_id"]),
        plan_revision=int(value["plan_revision"]),
        step_id=str(value["step_id"]),
        phase=CoordinationPhase(str(value["phase"])),
        dependency_ids=tuple(cast(list[str], value["dependency_ids"])),
        satisfied_dependency_ids=tuple(cast(list[str], value["satisfied_dependency_ids"])),
        latest_run_id=cast(str | None, value.get("latest_run_id")),
        current_attempt=int(value["current_attempt"]),
        retry_policy=StepRetryPolicy(
            max_attempts=int(retry["max_attempts"]),
            initial_delay_seconds=float(retry["initial_delay_seconds"]),
            multiplier=float(retry["multiplier"]),
            max_delay_seconds=float(retry["max_delay_seconds"]),
            retryable_categories=tuple(cast(list[str], retry["retryable_categories"])),
            version=int(retry["version"]),
        ),
        retry_due_at=_dt(cast(str | None, value.get("retry_due_at"))),
        wait=_wait_from_dict(cast(dict[str, Any] | None, value.get("wait"))),
        predecessor_failure_policy=PredecessorFailurePolicy(
            str(value["predecessor_failure_policy"])
        ),
        processed_keys=tuple(cast(list[str], value["processed_keys"])),
        reconciliation=ReconciliationDisposition(str(value["reconciliation"])),
        reconciliation_detail=cast(str | None, value.get("reconciliation_detail")),
        correlation_id=cast(str | None, value.get("correlation_id")),
        causation_id=cast(str | None, value.get("causation_id")),
        provenance_source=str(value["provenance_source"]),
        revision=int(value["revision"]),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
    )


class SQLiteCoordinatorRepository:
    """Versioned durable store with CAS updates and persisted fencing counters."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS coordinator_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coordinator_plans (
                    plan_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    store_revision INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coordinator_steps (
                    step_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    step_json TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES coordinator_plans(plan_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS coordinator_claims (
                    step_id TEXT PRIMARY KEY,
                    claim_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    fence INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    FOREIGN KEY(step_id) REFERENCES coordinator_steps(step_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS coordinator_fences (
                    step_id TEXT PRIMARY KEY,
                    fence INTEGER NOT NULL
                );
                """
            )
            row = connection.execute(
                "SELECT value FROM coordinator_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO coordinator_meta(key, value) VALUES('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
            elif int(row[0]) != _SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported coordinator schema {row[0]}; expected {_SCHEMA_VERSION}"
                )

    def create_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        records: tuple[StepCoordinationRecord, ...],
    ) -> PlanRuntimeState:
        state = PlanRuntimeState(plan=plan, steps=steps)
        if {item.step_id for item in records} != {item.id for item in steps}:
            raise ValueError("coordination records must cover every canonical Step exactly once")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json, store_revision FROM coordinator_plans WHERE plan_id = ?",
                (plan.id,),
            ).fetchone()
            if row is not None:
                existing = self.get_plan(plan.id)
                if existing.plan == plan and existing.steps == steps:
                    return existing
                raise ContractError(ErrorCode.CONFLICT, f"plan {plan.id} is already registered")
            connection.execute(
                "INSERT INTO coordinator_plans(plan_id, task_id, plan_json, store_revision) "
                "VALUES(?, ?, ?, 1)",
                (plan.id, plan.task_id, _dump(_plan_to_dict(plan))),
            )
            for step, record in zip(steps, records, strict=True):
                connection.execute(
                    "INSERT INTO coordinator_steps(step_id, plan_id, step_json, record_json, revision) "
                    "VALUES(?, ?, ?, ?, ?)",
                    (
                        step.id,
                        plan.id,
                        _dump(_step_to_dict(step)),
                        _dump(_record_to_dict(record)),
                        record.revision,
                    ),
                )
            return state

    def get_plan(self, plan_id: str) -> PlanRuntimeState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT plan_json, store_revision FROM coordinator_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"coordination plan {plan_id} not found")
            steps = tuple(
                _step_from_dict(_load(item[0]))
                for item in connection.execute(
                    "SELECT step_json FROM coordinator_steps WHERE plan_id = ? ORDER BY step_id",
                    (plan_id,),
                ).fetchall()
            )
            return PlanRuntimeState(
                plan=_plan_from_dict(_load(row[0])), steps=steps, store_revision=int(row[1])
            )

    def get_step_record(self, step_id: str) -> StepCoordinationRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json FROM coordinator_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"coordination Step {step_id} not found")
            return _record_from_dict(_load(row[0]))

    def list_step_records(self, plan_id: str) -> tuple[StepCoordinationRecord, ...]:
        self.get_plan(plan_id)
        with self._connect() as connection:
            return tuple(
                _record_from_dict(_load(row[0]))
                for row in connection.execute(
                    "SELECT record_json FROM coordinator_steps WHERE plan_id = ? ORDER BY step_id",
                    (plan_id,),
                ).fetchall()
            )

    def list_active_plans(self) -> tuple[PlanRuntimeState, ...]:
        with self._connect() as connection:
            ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT plan_id FROM coordinator_plans ORDER BY plan_id"
                ).fetchall()
            )
        return tuple(self.get_plan(plan_id) for plan_id in ids)

    def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord:
        if step.id != record.step_id or step.plan_id != record.plan_id:
            raise ValueError("canonical Step and coordination record identity do not match")
        commit_time = now or datetime.now(UTC)
        if commit_time.tzinfo is None:
            raise ValueError("coordinator commit time must be timezone-aware")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if claim is not None:
                claim_row = connection.execute(
                    "SELECT claim_id, owner_id, fence, expires_at FROM coordinator_claims "
                    "WHERE step_id = ?",
                    (step.id,),
                ).fetchone()
                if (
                    claim_row is None
                    or str(claim_row[0]) != claim.claim_id
                    or str(claim_row[1]) != claim.owner_id
                    or int(claim_row[2]) != claim.fence
                    or datetime.fromisoformat(str(claim_row[3])) <= commit_time
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "stale or expired coordinator claim",
                        details={"step_id": step.id, "fence": claim.fence},
                    )
            row = connection.execute(
                "SELECT revision FROM coordinator_steps WHERE step_id = ?", (step.id,)
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"coordination Step {step.id} not found")
            current_revision = int(row[0])
            if current_revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "stale coordinator revision",
                    details={
                        "step_id": step.id,
                        "expected_revision": expected_revision,
                        "current_revision": current_revision,
                    },
                )
            saved = replace(record, revision=current_revision + 1, updated_at=commit_time)
            updated = connection.execute(
                "UPDATE coordinator_steps SET step_json = ?, record_json = ?, revision = ? "
                "WHERE step_id = ? AND revision = ?",
                (
                    _dump(_step_to_dict(step)),
                    _dump(_record_to_dict(saved)),
                    saved.revision,
                    step.id,
                    expected_revision,
                ),
            )
            if updated.rowcount != 1:
                raise ContractError(ErrorCode.CONFLICT, "concurrent coordinator update")
            connection.execute(
                "UPDATE coordinator_plans SET store_revision = store_revision + 1 WHERE plan_id = ?",
                (step.plan_id,),
            )
            return saved

    def acquire_claim(
        self,
        *,
        step_id: str,
        owner_id: str,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        if ttl.total_seconds() <= 0:
            raise ValueError("claim ttl must be positive")
        if now.tzinfo is None:
            raise ValueError("claim time must be timezone-aware")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM coordinator_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            if exists is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"coordination Step {step_id} not found")
            current = connection.execute(
                "SELECT claim_id, owner_id, fence, expires_at FROM coordinator_claims "
                "WHERE step_id = ?",
                (step_id,),
            ).fetchone()
            if current is not None:
                expiry = datetime.fromisoformat(str(current[3]))
                if expiry > now and str(current[1]) != owner_id:
                    return None
            fence_row = connection.execute(
                "SELECT fence FROM coordinator_fences WHERE step_id = ?", (step_id,)
            ).fetchone()
            fence = (int(fence_row[0]) if fence_row else 0) + 1
            connection.execute(
                "INSERT INTO coordinator_fences(step_id, fence) VALUES(?, ?) "
                "ON CONFLICT(step_id) DO UPDATE SET fence = excluded.fence",
                (step_id, fence),
            )
            claim = CoordinatorClaim(
                step_id=step_id,
                claim_id=f"claim-{uuid4()}",
                owner_id=owner_id,
                fence=fence,
                expires_at=now + ttl,
            )
            connection.execute(
                "INSERT INTO coordinator_claims(step_id, claim_id, owner_id, fence, expires_at) "
                "VALUES(?, ?, ?, ?, ?) ON CONFLICT(step_id) DO UPDATE SET "
                "claim_id=excluded.claim_id, owner_id=excluded.owner_id, fence=excluded.fence, "
                "expires_at=excluded.expires_at",
                (
                    claim.step_id,
                    claim.claim_id,
                    claim.owner_id,
                    claim.fence,
                    claim.expires_at.isoformat(),
                ),
            )
            return claim

    def renew_claim(
        self,
        *,
        claim: CoordinatorClaim,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        if ttl.total_seconds() <= 0:
            raise ValueError("claim ttl must be positive")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT claim_id, owner_id, fence, expires_at FROM coordinator_claims "
                "WHERE step_id = ?",
                (claim.step_id,),
            ).fetchone()
            if row is None:
                return None
            if (
                str(row[0]) != claim.claim_id
                or str(row[1]) != claim.owner_id
                or int(row[2]) != claim.fence
                or datetime.fromisoformat(str(row[3])) <= now
            ):
                return None
            renewed = replace(claim, expires_at=now + ttl)
            connection.execute(
                "UPDATE coordinator_claims SET expires_at = ? WHERE step_id = ?",
                (renewed.expires_at.isoformat(), claim.step_id),
            )
            return renewed

    def release_claim(self, claim: CoordinatorClaim) -> bool:
        with self._lock, self._connect() as connection:
            result = connection.execute(
                "DELETE FROM coordinator_claims WHERE step_id = ? AND claim_id = ? "
                "AND owner_id = ? AND fence = ?",
                (claim.step_id, claim.claim_id, claim.owner_id, claim.fence),
            )
            return result.rowcount == 1
