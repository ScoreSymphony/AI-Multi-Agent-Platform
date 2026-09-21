"""Durable atomic reservation state for per-Task execution budget enforcement."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from threading import RLock

from .models import (
    BudgetActionKind,
    BudgetConsumptionSource,
    BudgetDimension,
    BudgetExhaustionAction,
    BudgetReservation,
    ReservationState,
    TaskBudgetLimit,
    TaskBudgetPolicy,
    UnavailableMetricPolicy,
)


@dataclass(frozen=True, slots=True)
class ReservationClaim:
    """Atomic reservation constraint evaluated by the durable store."""

    reservation: BudgetReservation
    limit: float
    external_consumed: float = 0.0
    include_runtime_counter: bool = False


class TaskBudgetStore(ABC):
    @abstractmethod
    def put_policy(self, policy: TaskBudgetPolicy) -> None: ...

    @abstractmethod
    def get_policy(self, task_id: str) -> TaskBudgetPolicy | None: ...

    @abstractmethod
    def list_policy_versions(self, task_id: str) -> tuple[TaskBudgetPolicy, ...]: ...

    @abstractmethod
    def try_reserve_many(self, claims: tuple[ReservationClaim, ...]) -> bool: ...

    @abstractmethod
    def get_reservation(self, reservation_id: str) -> BudgetReservation | None: ...

    @abstractmethod
    def list_reservations(self, task_id: str) -> tuple[BudgetReservation, ...]: ...

    @abstractmethod
    def release_reservation(self, reservation_id: str) -> BudgetReservation: ...

    @abstractmethod
    def reconcile_reservation(
        self,
        reservation_id: str,
        *,
        consumed_quantity: float,
        add_to_runtime_counter: bool,
    ) -> BudgetReservation: ...

    @abstractmethod
    def runtime_counter(self, task_id: str, dimension: BudgetDimension) -> float: ...

    @abstractmethod
    def runtime_counter_snapshot(
        self,
        task_id: str,
        dimension: BudgetDimension,
        observed_at: datetime,
    ) -> tuple[float, float]:
        """Return consumed runtime-counter usage and active reserved quantity atomically."""
        ...

    @abstractmethod
    def expire_reservations(self, observed_at: datetime) -> tuple[BudgetReservation, ...]: ...


class InMemoryTaskBudgetStore(TaskBudgetStore):
    def __init__(self) -> None:
        self._policies: dict[str, TaskBudgetPolicy] = {}
        self._history: dict[str, dict[int, TaskBudgetPolicy]] = {}
        self._reservations: dict[str, BudgetReservation] = {}
        self._counters: dict[tuple[str, BudgetDimension], float] = {}
        self._lock = RLock()

    def put_policy(self, policy: TaskBudgetPolicy) -> None:
        with self._lock:
            current = self._policies.get(policy.task_id)
            _validate_policy_revision(current, policy)
            if current == policy:
                return
            self._policies[policy.task_id] = policy
            self._history.setdefault(policy.task_id, {})[policy.version] = policy

    def get_policy(self, task_id: str) -> TaskBudgetPolicy | None:
        with self._lock:
            return self._policies.get(task_id)

    def list_policy_versions(self, task_id: str) -> tuple[TaskBudgetPolicy, ...]:
        with self._lock:
            versions = tuple(self._history.get(task_id, {}).values())
        return tuple(sorted(versions, key=lambda policy: policy.version))

    def try_reserve_many(self, claims: tuple[ReservationClaim, ...]) -> bool:
        if not claims:
            return True
        _validate_claim_batch(claims)
        observed_at = max(claim.reservation.created_at for claim in claims)
        with self._lock:
            self._expire_locked(observed_at)
            pending: dict[tuple[str, BudgetDimension], float] = {}
            for claim in claims:
                reservation = claim.reservation
                key = (reservation.task_id, reservation.dimension)
                active = sum(
                    item.quantity
                    for item in self._reservations.values()
                    if item.task_id == reservation.task_id
                    and item.dimension is reservation.dimension
                    and item.state is ReservationState.ACTIVE
                )
                counter = self._counters.get(key, 0.0) if claim.include_runtime_counter else 0.0
                requested = pending.get(key, 0.0) + reservation.quantity
                if claim.external_consumed + counter + active + requested > claim.limit:
                    return False
                pending[key] = requested
            for claim in claims:
                reservation = claim.reservation
                current = self._reservations.get(reservation.id)
                if current is not None and current != reservation:
                    raise ValueError("reservation ID is immutable once stored")
                self._reservations[reservation.id] = reservation
            return True

    def get_reservation(self, reservation_id: str) -> BudgetReservation | None:
        with self._lock:
            return self._reservations.get(reservation_id)

    def list_reservations(self, task_id: str) -> tuple[BudgetReservation, ...]:
        with self._lock:
            items = tuple(
                reservation
                for reservation in self._reservations.values()
                if reservation.task_id == task_id
            )
        return tuple(sorted(items, key=lambda item: (item.created_at, item.id)))

    def release_reservation(self, reservation_id: str) -> BudgetReservation:
        with self._lock:
            reservation = self._required_reservation(reservation_id)
            if reservation.state is not ReservationState.ACTIVE:
                return reservation
            released = replace(reservation, state=ReservationState.RELEASED)
            self._reservations[reservation_id] = released
            return released

    def reconcile_reservation(
        self,
        reservation_id: str,
        *,
        consumed_quantity: float,
        add_to_runtime_counter: bool,
    ) -> BudgetReservation:
        if consumed_quantity < 0:
            raise ValueError("consumed_quantity must be non-negative")
        with self._lock:
            reservation = self._required_reservation(reservation_id)
            if reservation.state is ReservationState.RECONCILED:
                if reservation.reconciled_quantity != consumed_quantity:
                    raise ValueError("reconciled reservation quantity is immutable")
                return reservation
            if reservation.state is not ReservationState.ACTIVE:
                raise ValueError("only an active reservation can be reconciled")
            reconciled = replace(
                reservation,
                state=ReservationState.RECONCILED,
                reconciled_quantity=consumed_quantity,
            )
            if add_to_runtime_counter:
                key = (reservation.task_id, reservation.dimension)
                self._counters[key] = self._counters.get(key, 0.0) + consumed_quantity
            self._reservations[reservation_id] = reconciled
            return reconciled

    def runtime_counter(self, task_id: str, dimension: BudgetDimension) -> float:
        with self._lock:
            return self._counters.get((task_id, dimension), 0.0)

    def runtime_counter_snapshot(
        self,
        task_id: str,
        dimension: BudgetDimension,
        observed_at: datetime,
    ) -> tuple[float, float]:
        with self._lock:
            self._expire_locked(observed_at)
            consumed = self._counters.get((task_id, dimension), 0.0)
            reserved = sum(
                reservation.quantity
                for reservation in self._reservations.values()
                if reservation.task_id == task_id
                and reservation.dimension is dimension
                and reservation.state is ReservationState.ACTIVE
            )
            return consumed, reserved

    def expire_reservations(self, observed_at: datetime) -> tuple[BudgetReservation, ...]:
        with self._lock:
            return self._expire_locked(observed_at)

    def _expire_locked(self, observed_at: datetime) -> tuple[BudgetReservation, ...]:
        expired: list[BudgetReservation] = []
        for reservation_id, reservation in tuple(self._reservations.items()):
            if (
                reservation.state is ReservationState.ACTIVE
                and reservation.expires_at is not None
                and reservation.expires_at <= observed_at
            ):
                updated = replace(reservation, state=ReservationState.EXPIRED)
                self._reservations[reservation_id] = updated
                expired.append(updated)
        return tuple(expired)

    def _required_reservation(self, reservation_id: str) -> BudgetReservation:
        reservation = self._reservations.get(reservation_id)
        if reservation is None:
            raise KeyError(reservation_id)
        return reservation


class SQLiteTaskBudgetStore(TaskBudgetStore):
    """Dependency-free restart-safe reference store with atomic reservation admission."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS task_budget_policies (
                    task_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_budget_policy_history (
                    task_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(task_id, version)
                );
                CREATE TABLE IF NOT EXISTS task_budget_reservations (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    state TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    expires_at TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS task_budget_reservation_scope_idx
                    ON task_budget_reservations(task_id, dimension, state);
                CREATE TABLE IF NOT EXISTS task_budget_runtime_counters (
                    task_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    PRIMARY KEY(task_id, dimension)
                );
                """
            )

    def put_policy(self, policy: TaskBudgetPolicy) -> None:
        payload = _dump(_policy_to_json(policy))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM task_budget_policies WHERE task_id = ?",
                (policy.task_id,),
            ).fetchone()
            current = None if row is None else _policy_from_json(str(row["payload"]))
            _validate_policy_revision(current, policy)
            if current == policy:
                return
            connection.execute(
                """
                INSERT OR REPLACE INTO task_budget_policies(task_id, version, payload)
                VALUES (?, ?, ?)
                """,
                (policy.task_id, policy.version, payload),
            )
            connection.execute(
                """
                INSERT INTO task_budget_policy_history(task_id, version, payload)
                VALUES (?, ?, ?)
                """,
                (policy.task_id, policy.version, payload),
            )

    def get_policy(self, task_id: str) -> TaskBudgetPolicy | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM task_budget_policies WHERE task_id = ?", (task_id,)
            ).fetchone()
        return None if row is None else _policy_from_json(str(row["payload"]))

    def list_policy_versions(self, task_id: str) -> tuple[TaskBudgetPolicy, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM task_budget_policy_history
                WHERE task_id = ? ORDER BY version
                """,
                (task_id,),
            ).fetchall()
        return tuple(_policy_from_json(str(row["payload"])) for row in rows)

    def try_reserve_many(self, claims: tuple[ReservationClaim, ...]) -> bool:
        if not claims:
            return True
        _validate_claim_batch(claims)
        observed_at = max(claim.reservation.created_at for claim in claims)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_connection(connection, observed_at)
            pending: dict[tuple[str, BudgetDimension], float] = {}
            for claim in claims:
                reservation = claim.reservation
                key = (reservation.task_id, reservation.dimension)
                row = connection.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0.0) AS quantity
                    FROM task_budget_reservations
                    WHERE task_id = ? AND dimension = ? AND state = ?
                    """,
                    (
                        reservation.task_id,
                        reservation.dimension.value,
                        ReservationState.ACTIVE.value,
                    ),
                ).fetchone()
                active = 0.0 if row is None else float(row["quantity"])
                counter = 0.0
                if claim.include_runtime_counter:
                    counter_row = connection.execute(
                        """
                        SELECT quantity FROM task_budget_runtime_counters
                        WHERE task_id = ? AND dimension = ?
                        """,
                        (reservation.task_id, reservation.dimension.value),
                    ).fetchone()
                    if counter_row is not None:
                        counter = float(counter_row["quantity"])
                requested = pending.get(key, 0.0) + reservation.quantity
                if claim.external_consumed + counter + active + requested > claim.limit:
                    return False
                pending[key] = requested
            for claim in claims:
                reservation = claim.reservation
                row = connection.execute(
                    "SELECT payload FROM task_budget_reservations WHERE id = ?",
                    (reservation.id,),
                ).fetchone()
                if row is not None:
                    current = _reservation_from_json(str(row["payload"]))
                    if current != reservation:
                        raise ValueError("reservation ID is immutable once stored")
                    continue
                connection.execute(
                    """
                    INSERT INTO task_budget_reservations(
                        id, task_id, dimension, state, quantity, expires_at, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    _reservation_row(reservation),
                )
            return True

    def get_reservation(self, reservation_id: str) -> BudgetReservation | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM task_budget_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
        return None if row is None else _reservation_from_json(str(row["payload"]))

    def list_reservations(self, task_id: str) -> tuple[BudgetReservation, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM task_budget_reservations
                WHERE task_id = ? ORDER BY id
                """,
                (task_id,),
            ).fetchall()
        return tuple(_reservation_from_json(str(row["payload"])) for row in rows)

    def release_reservation(self, reservation_id: str) -> BudgetReservation:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._required_connection_reservation(connection, reservation_id)
            if reservation.state is not ReservationState.ACTIVE:
                return reservation
            released = replace(reservation, state=ReservationState.RELEASED)
            self._update_connection_reservation(connection, released)
            return released

    def reconcile_reservation(
        self,
        reservation_id: str,
        *,
        consumed_quantity: float,
        add_to_runtime_counter: bool,
    ) -> BudgetReservation:
        if consumed_quantity < 0:
            raise ValueError("consumed_quantity must be non-negative")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._required_connection_reservation(connection, reservation_id)
            if reservation.state is ReservationState.RECONCILED:
                if reservation.reconciled_quantity != consumed_quantity:
                    raise ValueError("reconciled reservation quantity is immutable")
                return reservation
            if reservation.state is not ReservationState.ACTIVE:
                raise ValueError("only an active reservation can be reconciled")
            reconciled = replace(
                reservation,
                state=ReservationState.RECONCILED,
                reconciled_quantity=consumed_quantity,
            )
            if add_to_runtime_counter:
                row = connection.execute(
                    """
                    SELECT quantity FROM task_budget_runtime_counters
                    WHERE task_id = ? AND dimension = ?
                    """,
                    (reservation.task_id, reservation.dimension.value),
                ).fetchone()
                current = 0.0 if row is None else float(row["quantity"])
                connection.execute(
                    """
                    INSERT OR REPLACE INTO task_budget_runtime_counters(
                        task_id, dimension, quantity
                    ) VALUES (?, ?, ?)
                    """,
                    (reservation.task_id, reservation.dimension.value, current + consumed_quantity),
                )
            self._update_connection_reservation(connection, reconciled)
            return reconciled

    def runtime_counter(self, task_id: str, dimension: BudgetDimension) -> float:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT quantity FROM task_budget_runtime_counters
                WHERE task_id = ? AND dimension = ?
                """,
                (task_id, dimension.value),
            ).fetchone()
        return 0.0 if row is None else float(row["quantity"])

    def runtime_counter_snapshot(
        self,
        task_id: str,
        dimension: BudgetDimension,
        observed_at: datetime,
    ) -> tuple[float, float]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_connection(connection, observed_at)
            counter_row = connection.execute(
                """
                SELECT quantity FROM task_budget_runtime_counters
                WHERE task_id = ? AND dimension = ?
                """,
                (task_id, dimension.value),
            ).fetchone()
            reserved_row = connection.execute(
                """
                SELECT COALESCE(SUM(quantity), 0.0) AS quantity
                FROM task_budget_reservations
                WHERE task_id = ? AND dimension = ? AND state = ?
                """,
                (task_id, dimension.value, ReservationState.ACTIVE.value),
            ).fetchone()
            consumed = 0.0 if counter_row is None else float(counter_row["quantity"])
            reserved = 0.0 if reserved_row is None else float(reserved_row["quantity"])
            return consumed, reserved

    def expire_reservations(self, observed_at: datetime) -> tuple[BudgetReservation, ...]:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._expire_connection(connection, observed_at)

    def _expire_connection(
        self,
        connection: sqlite3.Connection,
        observed_at: datetime,
    ) -> tuple[BudgetReservation, ...]:
        rows = connection.execute(
            """
            SELECT payload FROM task_budget_reservations
            WHERE state = ? AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (ReservationState.ACTIVE.value, observed_at.isoformat()),
        ).fetchall()
        expired: list[BudgetReservation] = []
        for row in rows:
            reservation = _reservation_from_json(str(row["payload"]))
            updated = replace(reservation, state=ReservationState.EXPIRED)
            self._update_connection_reservation(connection, updated)
            expired.append(updated)
        return tuple(expired)

    def _required_connection_reservation(
        self,
        connection: sqlite3.Connection,
        reservation_id: str,
    ) -> BudgetReservation:
        row = connection.execute(
            "SELECT payload FROM task_budget_reservations WHERE id = ?", (reservation_id,)
        ).fetchone()
        if row is None:
            raise KeyError(reservation_id)
        return _reservation_from_json(str(row["payload"]))

    def _update_connection_reservation(
        self,
        connection: sqlite3.Connection,
        reservation: BudgetReservation,
    ) -> None:
        connection.execute(
            """
            UPDATE task_budget_reservations
            SET state = ?, expires_at = ?, payload = ?
            WHERE id = ?
            """,
            (
                reservation.state.value,
                None if reservation.expires_at is None else reservation.expires_at.isoformat(),
                _dump(_reservation_to_json(reservation)),
                reservation.id,
            ),
        )


def _validate_claim_batch(claims: tuple[ReservationClaim, ...]) -> None:
    ids: set[str] = set()
    for claim in claims:
        if claim.limit <= 0:
            raise ValueError("reservation claim limit must be greater than zero")
        if claim.external_consumed < 0:
            raise ValueError("external_consumed must be non-negative")
        if claim.reservation.state is not ReservationState.ACTIVE:
            raise ValueError("only active reservations may be admitted")
        if claim.reservation.id in ids:
            raise ValueError("reservation batch contains duplicate IDs")
        ids.add(claim.reservation.id)


def _validate_policy_revision(
    current: TaskBudgetPolicy | None,
    candidate: TaskBudgetPolicy,
) -> None:
    if current is None:
        if candidate.version != 1:
            raise ValueError("new Task budget policy must start at version 1")
        return
    if candidate.version == current.version:
        if candidate != current:
            raise ValueError("Task budget policy version is immutable once stored")
        return
    if candidate.version != current.version + 1:
        raise ValueError("Task budget policy version must advance exactly by one")


def _dump(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _policy_to_json(policy: TaskBudgetPolicy) -> dict[str, object]:
    return {
        "task_id": policy.task_id,
        "limits": [_limit_to_json(limit) for limit in policy.limits],
        "started_at": policy.started_at.isoformat(),
        "version": policy.version,
        "provenance": dict(policy.provenance),
        "updated_at": policy.updated_at.isoformat(),
    }


def _policy_from_json(raw: str) -> TaskBudgetPolicy:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid Task budget policy payload")
    limits_raw = payload["limits"]
    if not isinstance(limits_raw, list):
        raise ValueError("invalid Task budget limits payload")
    provenance = payload.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("invalid Task budget provenance payload")
    return TaskBudgetPolicy(
        task_id=str(payload["task_id"]),
        limits=tuple(_limit_from_json(item) for item in limits_raw),
        started_at=datetime.fromisoformat(str(payload["started_at"])),
        version=int(payload["version"]),
        provenance=dict(provenance),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
    )


def _limit_to_json(limit: TaskBudgetLimit) -> dict[str, object]:
    return {
        "dimension": limit.dimension.value,
        "limit": limit.limit,
        "source": limit.source.value,
        "metric_type": limit.metric_type,
        "unit": limit.unit,
        "warning_fraction": limit.warning_fraction,
        "include_estimated": limit.include_estimated,
        "exhaustion_action": limit.exhaustion_action.value,
        "unavailable_policy": limit.unavailable_policy.value,
    }


def _limit_from_json(value: object) -> TaskBudgetLimit:
    if not isinstance(value, dict):
        raise ValueError("invalid Task budget limit payload")
    return TaskBudgetLimit(
        dimension=BudgetDimension(str(value["dimension"])),
        limit=float(value["limit"]),
        source=BudgetConsumptionSource(str(value["source"])),
        metric_type=None if value.get("metric_type") is None else str(value["metric_type"]),
        unit=None if value.get("unit") is None else str(value["unit"]),
        warning_fraction=float(value["warning_fraction"]),
        include_estimated=bool(value["include_estimated"]),
        exhaustion_action=BudgetExhaustionAction(str(value["exhaustion_action"])),
        unavailable_policy=UnavailableMetricPolicy(str(value["unavailable_policy"])),
    )


def _reservation_row(reservation: BudgetReservation) -> tuple[object, ...]:
    return (
        reservation.id,
        reservation.task_id,
        reservation.dimension.value,
        reservation.state.value,
        reservation.quantity,
        None if reservation.expires_at is None else reservation.expires_at.isoformat(),
        _dump(_reservation_to_json(reservation)),
    )


def _reservation_to_json(reservation: BudgetReservation) -> dict[str, object]:
    return {
        "id": reservation.id,
        "task_id": reservation.task_id,
        "dimension": reservation.dimension.value,
        "quantity": reservation.quantity,
        "action": reservation.action.value,
        "state": reservation.state.value,
        "run_id": reservation.run_id,
        "step_id": reservation.step_id,
        "agent_id": reservation.agent_id,
        "agent_run_id": reservation.agent_run_id,
        "correlation_id": reservation.correlation_id,
        "causation_id": reservation.causation_id,
        "created_at": reservation.created_at.isoformat(),
        "expires_at": None
        if reservation.expires_at is None
        else reservation.expires_at.isoformat(),
        "reconciled_quantity": reservation.reconciled_quantity,
        "provenance": dict(reservation.provenance),
    }


def _reservation_from_json(raw: str) -> BudgetReservation:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid Task budget reservation payload")
    provenance = payload.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("invalid reservation provenance payload")
    expires_at = payload.get("expires_at")
    reconciled_quantity = payload.get("reconciled_quantity")
    return BudgetReservation(
        id=str(payload["id"]),
        task_id=str(payload["task_id"]),
        dimension=BudgetDimension(str(payload["dimension"])),
        quantity=float(payload["quantity"]),
        action=BudgetActionKind(str(payload["action"])),
        state=ReservationState(str(payload["state"])),
        run_id=None if payload.get("run_id") is None else str(payload["run_id"]),
        step_id=None if payload.get("step_id") is None else str(payload["step_id"]),
        agent_id=None if payload.get("agent_id") is None else str(payload["agent_id"]),
        agent_run_id=(
            None if payload.get("agent_run_id") is None else str(payload["agent_run_id"])
        ),
        correlation_id=(
            None if payload.get("correlation_id") is None else str(payload["correlation_id"])
        ),
        causation_id=(
            None if payload.get("causation_id") is None else str(payload["causation_id"])
        ),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        expires_at=None if expires_at is None else datetime.fromisoformat(str(expires_at)),
        reconciled_quantity=(None if reconciled_quantity is None else float(reconciled_quantity)),
        provenance=dict(provenance),
    )


__all__ = [
    "InMemoryTaskBudgetStore",
    "ReservationClaim",
    "SQLiteTaskBudgetStore",
    "TaskBudgetStore",
]
