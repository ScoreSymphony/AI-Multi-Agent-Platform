"""Persistence seam and reference repositories for canonical Agent Handoffs."""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import (
    AgentHandoff,
    HandoffConsumption,
    consumption_from_dict,
    consumption_to_dict,
    handoff_from_dict,
    handoff_to_dict,
)


class HandoffRepository(Protocol):
    def create_handoff(
        self,
        handoff: AgentHandoff,
        *,
        idempotency_key: str,
        request_digest: str,
        expected_previous_revision: int,
    ) -> tuple[AgentHandoff, bool]: ...

    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff: ...

    def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]: ...

    def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]: ...

    def bind_consumption(
        self, consumption: HandoffConsumption
    ) -> tuple[HandoffConsumption, bool]: ...

    def list_consumptions(
        self, handoff_id: str, revision: int
    ) -> tuple[HandoffConsumption, ...]: ...


class InMemoryHandoffRepository:
    """Deterministic reference repository preserving all immutable revisions."""

    def __init__(self) -> None:
        self._handoffs: dict[tuple[str, int], AgentHandoff] = {}
        self._idempotency: dict[str, tuple[str, str, int]] = {}
        self._consumptions: dict[tuple[str, int, str], HandoffConsumption] = {}

    def create_handoff(
        self,
        handoff: AgentHandoff,
        *,
        idempotency_key: str,
        request_digest: str,
        expected_previous_revision: int,
    ) -> tuple[AgentHandoff, bool]:
        existing_idempotency = self._idempotency.get(idempotency_key)
        if existing_idempotency is not None:
            existing_request_digest, existing_id, existing_revision = existing_idempotency
            if existing_request_digest != request_digest:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "handoff idempotency key was already used with different content",
                )
            return self.get_handoff(existing_id, existing_revision), False

        revisions = [
            revision
            for current_id, revision in self._handoffs
            if current_id == handoff.handoff_id
        ]
        latest = max(revisions, default=0)
        if latest != expected_previous_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "stale handoff revision expectation",
                details={"expected_previous_revision": expected_previous_revision, "latest": latest},
            )
        if handoff.revision != expected_previous_revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "handoff revision must increase exactly by one",
            )
        key = (handoff.handoff_id, handoff.revision)
        if key in self._handoffs:
            raise ContractError(ErrorCode.CONFLICT, "handoff revision already exists")
        self._handoffs[key] = handoff
        self._idempotency[idempotency_key] = (
            request_digest,
            handoff.handoff_id,
            handoff.revision,
        )
        return handoff, True

    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff:
        if revision is None:
            revisions = [
                current_revision
                for current_id, current_revision in self._handoffs
                if current_id == handoff_id
            ]
            if not revisions:
                raise ContractError(ErrorCode.NOT_FOUND, f"handoff not found: {handoff_id}")
            revision = max(revisions)
        try:
            return self._handoffs[(handoff_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"handoff revision not found: {handoff_id}@{revision}",
            ) from exc

    def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]:
        return tuple(
            sorted(
                (item for item in self._handoffs.values() if item.content.task_id == task_id),
                key=lambda item: (item.created_at, item.handoff_id, item.revision),
            )
        )

    def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]:
        return tuple(
            sorted(
                (
                    item
                    for item in self._handoffs.values()
                    if item.content.producer_step_id == step_id
                    or item.content.consumer_step_id == step_id
                ),
                key=lambda item: (item.created_at, item.handoff_id, item.revision),
            )
        )

    def bind_consumption(
        self, consumption: HandoffConsumption
    ) -> tuple[HandoffConsumption, bool]:
        handoff = self.get_handoff(consumption.handoff_id, consumption.handoff_revision)
        if handoff.content_digest != consumption.handoff_digest:
            raise ContractError(ErrorCode.CONFLICT, "handoff digest does not match stored revision")
        key = (
            consumption.handoff_id,
            consumption.handoff_revision,
            consumption.consuming_run_id,
        )
        existing = self._consumptions.get(key)
        if existing is not None:
            if existing != consumption:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "consuming Run is already bound to different handoff evidence",
                )
            return existing, False
        self._consumptions[key] = consumption
        return consumption, True

    def list_consumptions(
        self, handoff_id: str, revision: int
    ) -> tuple[HandoffConsumption, ...]:
        self.get_handoff(handoff_id, revision)
        return tuple(
            sorted(
                (
                    item
                    for (current_id, current_revision, _), item in self._consumptions.items()
                    if current_id == handoff_id and current_revision == revision
                ),
                key=lambda item: (item.consumed_at, item.consuming_run_id),
            )
        )


class SQLiteHandoffRepository:
    """Durable single-node repository with revision and idempotency protection."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path))
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_handoffs (
                    handoff_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    producer_step_id TEXT NOT NULL,
                    consumer_step_id TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (handoff_id, revision)
                );
                CREATE INDEX IF NOT EXISTS idx_agent_handoffs_task
                    ON agent_handoffs(task_id);
                CREATE INDEX IF NOT EXISTS idx_agent_handoffs_producer_step
                    ON agent_handoffs(producer_step_id);
                CREATE INDEX IF NOT EXISTS idx_agent_handoffs_consumer_step
                    ON agent_handoffs(consumer_step_id);

                CREATE TABLE IF NOT EXISTS agent_handoff_idempotency (
                    idempotency_key TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    handoff_id TEXT NOT NULL,
                    revision INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_handoff_consumptions (
                    handoff_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    consuming_run_id TEXT NOT NULL,
                    handoff_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (handoff_id, revision, consuming_run_id),
                    FOREIGN KEY (handoff_id, revision)
                        REFERENCES agent_handoffs(handoff_id, revision)
                );
                """
            )

    def create_handoff(
        self,
        handoff: AgentHandoff,
        *,
        idempotency_key: str,
        request_digest: str,
        expected_previous_revision: int,
    ) -> tuple[AgentHandoff, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            idem = connection.execute(
                "SELECT request_digest, handoff_id, revision "
                "FROM agent_handoff_idempotency WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if idem is not None:
                if str(idem["request_digest"]) != request_digest:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "handoff idempotency key was already used with different content",
                    )
                existing = self._get_handoff_with_connection(
                    connection,
                    str(idem["handoff_id"]),
                    int(idem["revision"]),
                )
                connection.commit()
                return existing, False

            latest_row = connection.execute(
                "SELECT MAX(revision) AS latest FROM agent_handoffs WHERE handoff_id = ?",
                (handoff.handoff_id,),
            ).fetchone()
            latest_value = latest_row["latest"] if latest_row is not None else None
            latest = int(latest_value) if latest_value is not None else 0
            if latest != expected_previous_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "stale handoff revision expectation",
                    details={
                        "expected_previous_revision": expected_previous_revision,
                        "latest": latest,
                    },
                )
            if handoff.revision != expected_previous_revision + 1:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "handoff revision must increase exactly by one",
                )
            payload = json.dumps(handoff_to_dict(handoff), sort_keys=True, separators=(",", ":"))
            try:
                connection.execute(
                    "INSERT INTO agent_handoffs "
                    "(handoff_id, revision, task_id, producer_step_id, consumer_step_id, "
                    "content_digest, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        handoff.handoff_id,
                        handoff.revision,
                        handoff.content.task_id,
                        handoff.content.producer_step_id,
                        handoff.content.consumer_step_id,
                        handoff.content_digest,
                        payload,
                    ),
                )
                connection.execute(
                    "INSERT INTO agent_handoff_idempotency "
                    "(idempotency_key, request_digest, handoff_id, revision) VALUES (?, ?, ?, ?)",
                    (
                        idempotency_key,
                        request_digest,
                        handoff.handoff_id,
                        handoff.revision,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ContractError(ErrorCode.CONFLICT, "handoff persistence conflict") from exc
            connection.commit()
            return handoff, True

    def get_handoff(self, handoff_id: str, revision: int | None = None) -> AgentHandoff:
        with self._connect() as connection:
            return self._get_handoff_with_connection(connection, handoff_id, revision)

    def _get_handoff_with_connection(
        self,
        connection: sqlite3.Connection,
        handoff_id: str,
        revision: int | None,
    ) -> AgentHandoff:
        if revision is None:
            row = connection.execute(
                "SELECT payload_json FROM agent_handoffs WHERE handoff_id = ? "
                "ORDER BY revision DESC LIMIT 1",
                (handoff_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT payload_json FROM agent_handoffs WHERE handoff_id = ? AND revision = ?",
                (handoff_id, revision),
            ).fetchone()
        if row is None:
            suffix = handoff_id if revision is None else f"{handoff_id}@{revision}"
            raise ContractError(ErrorCode.NOT_FOUND, f"handoff not found: {suffix}")
        payload = cast(Mapping[str, Any], json.loads(str(row["payload_json"])))
        return handoff_from_dict(payload)

    def list_handoffs_for_task(self, task_id: str) -> tuple[AgentHandoff, ...]:
        return self._list_handoffs(
            "SELECT payload_json FROM agent_handoffs WHERE task_id = ? "
            "ORDER BY handoff_id, revision",
            (task_id,),
        )

    def list_handoffs_for_step(self, step_id: str) -> tuple[AgentHandoff, ...]:
        return self._list_handoffs(
            "SELECT payload_json FROM agent_handoffs "
            "WHERE producer_step_id = ? OR consumer_step_id = ? ORDER BY handoff_id, revision",
            (step_id, step_id),
        )

    def _list_handoffs(self, query: str, parameters: tuple[object, ...]) -> tuple[AgentHandoff, ...]:
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return tuple(
            handoff_from_dict(
                cast(Mapping[str, Any], json.loads(str(row["payload_json"])))
            )
            for row in rows
        )

    def bind_consumption(
        self, consumption: HandoffConsumption
    ) -> tuple[HandoffConsumption, bool]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            handoff = self._get_handoff_with_connection(
                connection,
                consumption.handoff_id,
                consumption.handoff_revision,
            )
            if handoff.content_digest != consumption.handoff_digest:
                raise ContractError(ErrorCode.CONFLICT, "handoff digest does not match stored revision")
            row = connection.execute(
                "SELECT payload_json FROM agent_handoff_consumptions "
                "WHERE handoff_id = ? AND revision = ? AND consuming_run_id = ?",
                (
                    consumption.handoff_id,
                    consumption.handoff_revision,
                    consumption.consuming_run_id,
                ),
            ).fetchone()
            if row is not None:
                existing = consumption_from_dict(
                    cast(Mapping[str, Any], json.loads(str(row["payload_json"])))
                )
                if existing != consumption:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "consuming Run is already bound to different handoff evidence",
                    )
                connection.commit()
                return existing, False
            payload = json.dumps(
                consumption_to_dict(consumption), sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                "INSERT INTO agent_handoff_consumptions "
                "(handoff_id, revision, consuming_run_id, handoff_digest, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    consumption.handoff_id,
                    consumption.handoff_revision,
                    consumption.consuming_run_id,
                    consumption.handoff_digest,
                    payload,
                ),
            )
            connection.commit()
            return consumption, True

    def list_consumptions(
        self, handoff_id: str, revision: int
    ) -> tuple[HandoffConsumption, ...]:
        with self._connect() as connection:
            self._get_handoff_with_connection(connection, handoff_id, revision)
            rows = connection.execute(
                "SELECT payload_json FROM agent_handoff_consumptions "
                "WHERE handoff_id = ? AND revision = ? ORDER BY consuming_run_id",
                (handoff_id, revision),
            ).fetchall()
        return tuple(
            consumption_from_dict(
                cast(Mapping[str, Any], json.loads(str(row["payload_json"])))
            )
            for row in rows
        )
