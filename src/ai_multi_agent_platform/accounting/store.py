"""Durable and in-memory stores for canonical usage/accounting state."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from threading import Lock

from .models import (
    AggregationMode,
    BudgetAction,
    BudgetKind,
    MeasurementQuality,
    ThresholdLevel,
    UsageBudget,
    UsageQuery,
    UsageRecord,
    UsageScope,
)


class UsageStore(ABC):
    @abstractmethod
    def append(self, record: UsageRecord) -> bool: ...

    @abstractmethod
    def query(self, query: UsageQuery) -> tuple[UsageRecord, ...]: ...

    @abstractmethod
    def put_budget(self, budget: UsageBudget) -> None: ...

    @abstractmethod
    def list_budgets(self) -> tuple[UsageBudget, ...]: ...

    @abstractmethod
    def get_budget(self, budget_id: str) -> UsageBudget | None: ...

    @abstractmethod
    def list_budget_versions(self, budget_id: str) -> tuple[UsageBudget, ...]: ...

    @abstractmethod
    def get_threshold_level(self, budget_id: str) -> ThresholdLevel | None: ...

    @abstractmethod
    def set_threshold_level(self, budget_id: str, level: ThresholdLevel | None) -> None: ...

    @abstractmethod
    def get_threshold_generation(self, budget_id: str) -> int: ...

    @abstractmethod
    def advance_threshold_generation(self, budget_id: str) -> int: ...


class InMemoryUsageStore(UsageStore):
    def __init__(self) -> None:
        self._records: dict[str, UsageRecord] = {}
        self._budgets: dict[str, UsageBudget] = {}
        self._budget_history: dict[str, dict[int, UsageBudget]] = {}
        self._levels: dict[str, ThresholdLevel] = {}
        self._threshold_generations: dict[str, int] = {}
        self._lock = Lock()

    def append(self, record: UsageRecord) -> bool:
        with self._lock:
            current = self._records.get(record.id)
            if current is None:
                self._records[record.id] = record
                return True
            if current != record:
                raise ValueError("usage record ID is immutable once stored")
            return False

    def query(self, query: UsageQuery) -> tuple[UsageRecord, ...]:
        with self._lock:
            records = tuple(self._records.values())
        return tuple(record for record in records if _matches(record, query))

    def put_budget(self, budget: UsageBudget) -> None:
        with self._lock:
            current = self._budgets.get(budget.id)
            _validate_budget_revision(current, budget)
            if current == budget:
                return
            self._budgets[budget.id] = budget
            self._budget_history.setdefault(budget.id, {})[budget.version] = budget

    def list_budgets(self) -> tuple[UsageBudget, ...]:
        with self._lock:
            return tuple(self._budgets.values())

    def get_budget(self, budget_id: str) -> UsageBudget | None:
        with self._lock:
            return self._budgets.get(budget_id)

    def list_budget_versions(self, budget_id: str) -> tuple[UsageBudget, ...]:
        with self._lock:
            versions = tuple(self._budget_history.get(budget_id, {}).values())
        return tuple(sorted(versions, key=lambda budget: budget.version))

    def get_threshold_level(self, budget_id: str) -> ThresholdLevel | None:
        with self._lock:
            return self._levels.get(budget_id)

    def set_threshold_level(self, budget_id: str, level: ThresholdLevel | None) -> None:
        with self._lock:
            previous = self._levels.get(budget_id)
            if level is None:
                if previous is not None and budget_id not in self._threshold_generations:
                    self._threshold_generations[budget_id] = 1
                self._levels.pop(budget_id, None)
                return
            if previous is None:
                current = self._threshold_generations.get(budget_id, 0)
                self._threshold_generations[budget_id] = current + 1
            self._levels[budget_id] = level

    def get_threshold_generation(self, budget_id: str) -> int:
        with self._lock:
            generation = self._threshold_generations.get(budget_id)
            if generation is not None:
                return generation
            # Compatibility for stores populated before threshold episodes existed:
            # an active legacy threshold represents the first episode.
            return 1 if budget_id in self._levels else 0

    def advance_threshold_generation(self, budget_id: str) -> int:
        with self._lock:
            current = self._threshold_generations.get(budget_id)
            if current is None:
                current = 1 if budget_id in self._levels else 0
            current += 1
            self._threshold_generations[budget_id] = current
            return current


class SQLiteUsageStore(UsageStore):
    """Dependency-free durable reference store; provider replacement stays behind UsageStore."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS usage_records (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS usage_records_timestamp_idx
                    ON usage_records(timestamp);
                CREATE TABLE IF NOT EXISTS usage_budgets (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_budget_history (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY(id, version)
                );
                CREATE TABLE IF NOT EXISTS usage_threshold_state (
                    budget_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_threshold_generation (
                    budget_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL
                );
                """
            )

    def append(self, record: UsageRecord) -> bool:
        payload = json.dumps(_record_to_json(record), sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM usage_records WHERE id = ?", (record.id,)
            ).fetchone()
            if row is not None:
                current = _record_from_json(str(row["payload"]))
                if current != record:
                    raise ValueError("usage record ID is immutable once stored")
                return False
            connection.execute(
                "INSERT INTO usage_records(id, timestamp, payload) VALUES (?, ?, ?)",
                (record.id, record.timestamp.isoformat(), payload),
            )
        return True

    def query(self, query: UsageQuery) -> tuple[UsageRecord, ...]:
        clauses: list[str] = []
        parameters: list[str] = []
        if query.start is not None:
            clauses.append("timestamp >= ?")
            parameters.append(query.start.isoformat())
        if query.end is not None:
            clauses.append("timestamp <= ?")
            parameters.append(query.end.isoformat())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM usage_records{where} ORDER BY timestamp, id",
                parameters,
            ).fetchall()
        records = tuple(_record_from_json(str(row["payload"])) for row in rows)
        return tuple(record for record in records if _matches(record, query))

    def put_budget(self, budget: UsageBudget) -> None:
        payload = json.dumps(_budget_to_json(budget), sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM usage_budgets WHERE id = ?", (budget.id,)
            ).fetchone()
            current = None if row is None else _budget_from_json(str(row["payload"]))
            _validate_budget_revision(current, budget)
            if current == budget:
                return
            connection.execute(
                "INSERT OR REPLACE INTO usage_budgets(id, payload) VALUES (?, ?)",
                (budget.id, payload),
            )
            connection.execute(
                "INSERT INTO usage_budget_history(id, version, payload) VALUES (?, ?, ?)",
                (budget.id, budget.version, payload),
            )

    def list_budgets(self) -> tuple[UsageBudget, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute("SELECT payload FROM usage_budgets ORDER BY id").fetchall()
        return tuple(_budget_from_json(str(row["payload"])) for row in rows)

    def get_budget(self, budget_id: str) -> UsageBudget | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM usage_budgets WHERE id = ?", (budget_id,)
            ).fetchone()
        return None if row is None else _budget_from_json(str(row["payload"]))

    def list_budget_versions(self, budget_id: str) -> tuple[UsageBudget, ...]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM usage_budget_history WHERE id = ? ORDER BY version",
                (budget_id,),
            ).fetchall()
        return tuple(_budget_from_json(str(row["payload"])) for row in rows)

    def get_threshold_level(self, budget_id: str) -> ThresholdLevel | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT level FROM usage_threshold_state WHERE budget_id = ?", (budget_id,)
            ).fetchone()
        return None if row is None else ThresholdLevel(str(row["level"]))

    def set_threshold_level(self, budget_id: str, level: ThresholdLevel | None) -> None:
        with self._lock, self._connect() as connection:
            previous = connection.execute(
                "SELECT level FROM usage_threshold_state WHERE budget_id = ?",
                (budget_id,),
            ).fetchone()
            if level is None:
                if previous is not None:
                    generation = connection.execute(
                        "SELECT generation FROM usage_threshold_generation WHERE budget_id = ?",
                        (budget_id,),
                    ).fetchone()
                    if generation is None:
                        connection.execute(
                            """
                            INSERT INTO usage_threshold_generation(budget_id, generation)
                            VALUES (?, 1)
                            """,
                            (budget_id,),
                        )
                connection.execute(
                    "DELETE FROM usage_threshold_state WHERE budget_id = ?", (budget_id,)
                )
                return
            if previous is None:
                generation = connection.execute(
                    "SELECT generation FROM usage_threshold_generation WHERE budget_id = ?",
                    (budget_id,),
                ).fetchone()
                current = 0 if generation is None else int(generation["generation"])
                connection.execute(
                    """
                    INSERT OR REPLACE INTO usage_threshold_generation(budget_id, generation)
                    VALUES (?, ?)
                    """,
                    (budget_id, current + 1),
                )
            connection.execute(
                "INSERT OR REPLACE INTO usage_threshold_state(budget_id, level) VALUES (?, ?)",
                (budget_id, level.value),
            )

    def get_threshold_generation(self, budget_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT generation FROM usage_threshold_generation WHERE budget_id = ?",
                (budget_id,),
            ).fetchone()
            if row is not None:
                return int(row["generation"])
            legacy = connection.execute(
                "SELECT 1 FROM usage_threshold_state WHERE budget_id = ?",
                (budget_id,),
            ).fetchone()
        return 1 if legacy is not None else 0

    def advance_threshold_generation(self, budget_id: str) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT generation FROM usage_threshold_generation WHERE budget_id = ?",
                (budget_id,),
            ).fetchone()
            if row is None:
                legacy = connection.execute(
                    "SELECT 1 FROM usage_threshold_state WHERE budget_id = ?",
                    (budget_id,),
                ).fetchone()
                current = 1 if legacy is not None else 0
            else:
                current = int(row["generation"])
            generation = current + 1
            connection.execute(
                """
                INSERT OR REPLACE INTO usage_threshold_generation(budget_id, generation)
                VALUES (?, ?)
                """,
                (budget_id, generation),
            )
        return generation


def _matches(record: UsageRecord, query: UsageQuery) -> bool:
    if query.metric_type is not None and record.metric_type != query.metric_type:
        return False
    if query.unit is not None and record.unit != query.unit:
        return False
    if query.quality is not None and record.quality is not query.quality:
        return False
    if query.start is not None and record.timestamp < query.start:
        return False
    if query.end is not None and record.timestamp > query.end:
        return False
    required_scope = query.scope.fields()
    actual_scope = record.scope.fields()
    return all(actual_scope.get(name) == value for name, value in required_scope.items())


def _record_to_json(record: UsageRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["quality"] = record.quality.value
    payload["aggregation_mode"] = record.aggregation_mode.value
    payload["timestamp"] = record.timestamp.isoformat()
    payload["started_at"] = None if record.started_at is None else record.started_at.isoformat()
    payload["ended_at"] = None if record.ended_at is None else record.ended_at.isoformat()
    return payload


def _record_from_json(raw: str) -> UsageRecord:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid usage record payload")
    scope_raw = payload.pop("scope")
    if not isinstance(scope_raw, dict):
        raise ValueError("invalid usage scope payload")
    quality = MeasurementQuality(str(payload.pop("quality")))
    aggregation_mode = AggregationMode(
        str(payload.pop("aggregation_mode", AggregationMode.ADDITIVE.value))
    )
    timestamp = datetime.fromisoformat(str(payload.pop("timestamp")))
    started_raw = payload.pop("started_at")
    ended_raw = payload.pop("ended_at")
    return UsageRecord(
        **payload,
        scope=UsageScope(**scope_raw),
        quality=quality,
        aggregation_mode=aggregation_mode,
        timestamp=timestamp,
        started_at=None if started_raw is None else datetime.fromisoformat(str(started_raw)),
        ended_at=None if ended_raw is None else datetime.fromisoformat(str(ended_raw)),
    )


def _budget_to_json(budget: UsageBudget) -> dict[str, object]:
    payload = asdict(budget)
    payload["kind"] = budget.kind.value
    payload["action"] = budget.action.value
    payload["created_at"] = budget.created_at.isoformat()
    return payload


def _budget_from_json(raw: str) -> UsageBudget:
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("invalid budget payload")
    kind = BudgetKind(str(payload.pop("kind")))
    action = BudgetAction(str(payload.pop("action")))
    created_at = datetime.fromisoformat(str(payload.pop("created_at")))
    return UsageBudget(**payload, kind=kind, action=action, created_at=created_at)


def _validate_budget_revision(current: UsageBudget | None, candidate: UsageBudget) -> None:
    if current is None:
        if candidate.version != 1:
            raise ValueError("new budget must start at version 1")
        return
    if candidate.version == current.version:
        if candidate != current:
            raise ValueError("budget version is immutable once stored")
        return
    if candidate.version != current.version + 1:
        raise ValueError("budget version must advance exactly by one")
