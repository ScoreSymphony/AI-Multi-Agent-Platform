"""Persistence seams and deterministic SQLite reference storage for automations."""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import validate_id

from .models import (
    Automation,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    MissedSchedulePolicy,
    OverlapPolicy,
    RetryPolicy,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)


class AutomationRepository(ABC):
    @abstractmethod
    async def save_automation(self, automation: Automation) -> Automation: ...

    @abstractmethod
    async def get_automation(self, automation_id: str) -> Automation: ...

    @abstractmethod
    async def list_automations(self) -> tuple[Automation, ...]: ...

    @abstractmethod
    async def save_delivery(self, delivery: TriggerDelivery) -> TriggerDelivery: ...

    @abstractmethod
    async def get_delivery(self, delivery_id: str) -> TriggerDelivery: ...

    @abstractmethod
    async def find_delivery_by_dedupe(
        self, automation_id: str, dedupe_key: str
    ) -> TriggerDelivery | None: ...

    @abstractmethod
    async def list_deliveries(
        self, automation_id: str | None = None
    ) -> tuple[TriggerDelivery, ...]: ...


class InMemoryAutomationRepository(AutomationRepository):
    def __init__(self) -> None:
        self._automations: dict[str, Automation] = {}
        self._deliveries: dict[str, TriggerDelivery] = {}
        self._dedupe: dict[tuple[str, str], str] = {}

    async def save_automation(self, automation: Automation) -> Automation:
        self._automations[automation.id] = automation
        return automation

    async def get_automation(self, automation_id: str) -> Automation:
        validate_id(automation_id, "automation")
        try:
            return self._automations[automation_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"automation not found: {automation_id}"
            ) from exc

    async def list_automations(self) -> tuple[Automation, ...]:
        return tuple(self._automations.values())

    async def save_delivery(self, delivery: TriggerDelivery) -> TriggerDelivery:
        key = (delivery.automation_id, delivery.dedupe_key)
        existing_id = self._dedupe.get(key)
        if existing_id is not None and existing_id != delivery.id:
            return self._deliveries[existing_id]
        self._deliveries[delivery.id] = delivery
        self._dedupe[key] = delivery.id
        return delivery

    async def get_delivery(self, delivery_id: str) -> TriggerDelivery:
        validate_id(delivery_id, "trigger_delivery")
        try:
            return self._deliveries[delivery_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"trigger delivery not found: {delivery_id}"
            ) from exc

    async def find_delivery_by_dedupe(
        self, automation_id: str, dedupe_key: str
    ) -> TriggerDelivery | None:
        validate_id(automation_id, "automation")
        delivery_id = self._dedupe.get((automation_id, dedupe_key))
        return None if delivery_id is None else self._deliveries[delivery_id]

    async def list_deliveries(
        self, automation_id: str | None = None
    ) -> tuple[TriggerDelivery, ...]:
        if automation_id is None:
            return tuple(self._deliveries.values())
        validate_id(automation_id, "automation")
        return tuple(
            delivery
            for delivery in self._deliveries.values()
            if delivery.automation_id == automation_id
        )


class SqliteAutomationRepository(AutomationRepository):
    """Restart-safe reference repository with one dedupe constraint per automation."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS automations (
                        id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trigger_deliveries (
                        id TEXT PRIMARY KEY,
                        automation_id TEXT NOT NULL,
                        dedupe_key TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        UNIQUE(automation_id, dedupe_key)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_trigger_deliveries_automation
                    ON trigger_deliveries(automation_id)
                    """
                )
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to initialize automation storage"
            ) from exc

    async def save_automation(self, automation: Automation) -> Automation:
        encoded = json.dumps(_automation_json(automation), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO automations(id, payload) VALUES (?, ?)
                    ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                    """,
                    (automation.id, encoded),
                )
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to persist automation") from exc
        return automation

    async def get_automation(self, automation_id: str) -> Automation:
        validate_id(automation_id, "automation")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM automations WHERE id = ?", (automation_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read automation") from exc
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"automation not found: {automation_id}")
        return _automation_from_json(cast(str, row["payload"]))

    async def list_automations(self) -> tuple[Automation, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute("SELECT payload FROM automations ORDER BY id").fetchall()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to list automations") from exc
        return tuple(_automation_from_json(cast(str, row["payload"])) for row in rows)

    async def save_delivery(self, delivery: TriggerDelivery) -> TriggerDelivery:
        encoded = json.dumps(_delivery_json(delivery), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trigger_deliveries(id, automation_id, dedupe_key, payload)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET payload = excluded.payload
                    """,
                    (delivery.id, delivery.automation_id, delivery.dedupe_key, encoded),
                )
        except sqlite3.IntegrityError:
            existing = await self.find_delivery_by_dedupe(
                delivery.automation_id, delivery.dedupe_key
            )
            if existing is not None:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "trigger delivery dedupe conflict") from None
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to persist trigger delivery"
            ) from exc
        return delivery

    async def get_delivery(self, delivery_id: str) -> TriggerDelivery:
        validate_id(delivery_id, "trigger_delivery")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT payload FROM trigger_deliveries WHERE id = ?", (delivery_id,)
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(ErrorCode.BACKEND_ERROR, "failed to read trigger delivery") from exc
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"trigger delivery not found: {delivery_id}")
        return _delivery_from_json(cast(str, row["payload"]))

    async def find_delivery_by_dedupe(
        self, automation_id: str, dedupe_key: str
    ) -> TriggerDelivery | None:
        validate_id(automation_id, "automation")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM trigger_deliveries
                    WHERE automation_id = ? AND dedupe_key = ?
                    """,
                    (automation_id, dedupe_key),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to dedupe trigger delivery"
            ) from exc
        return None if row is None else _delivery_from_json(cast(str, row["payload"]))

    async def list_deliveries(
        self, automation_id: str | None = None
    ) -> tuple[TriggerDelivery, ...]:
        try:
            with self._connect() as connection:
                if automation_id is None:
                    rows = connection.execute(
                        "SELECT payload FROM trigger_deliveries ORDER BY id"
                    ).fetchall()
                else:
                    validate_id(automation_id, "automation")
                    rows = connection.execute(
                        """
                        SELECT payload FROM trigger_deliveries
                        WHERE automation_id = ? ORDER BY id
                        """,
                        (automation_id,),
                    ).fetchall()
        except sqlite3.Error as exc:
            raise ContractError(
                ErrorCode.BACKEND_ERROR, "failed to list trigger deliveries"
            ) from exc
        return tuple(_delivery_from_json(cast(str, row["payload"])) for row in rows)


def _automation_json(automation: Automation) -> dict[str, JsonValue]:
    trigger: dict[str, JsonValue] = {
        "type": automation.trigger.type.value,
        "timezone": automation.trigger.timezone,
        "at": None if automation.trigger.at is None else automation.trigger.at.isoformat(),
        "interval_seconds": automation.trigger.interval_seconds,
        "event_type": automation.trigger.event_type,
        "filters": dict(automation.trigger.filters),
        "webhook_source": automation.trigger.webhook_source,
        "verification_ref": automation.trigger.verification_ref,
        "missed_schedule_policy": automation.trigger.missed_schedule_policy.value,
    }
    return {
        "id": automation.id,
        "name": automation.name,
        "description": automation.description,
        "identity": {
            "principal_ref": automation.identity.principal_ref,
            "owner_type": automation.identity.owner_type,
            "owner_id": automation.identity.owner_id,
        },
        "trigger": trigger,
        "task_template": {
            "title": automation.task_template.title,
            "objective": automation.task_template.objective,
            "project_id": automation.task_template.project_id,
            "workspace_id": automation.task_template.workspace_id,
            "payload": dict(automation.task_template.payload),
        },
        "project_id": automation.project_id,
        "workspace_id": automation.workspace_id,
        "state": automation.state.value,
        "deduplication_strategy": automation.deduplication_strategy,
        "retry_policy": {
            "max_attempts": automation.retry_policy.max_attempts,
            "base_backoff_seconds": automation.retry_policy.base_backoff_seconds,
        },
        "overlap_policy": automation.overlap_policy.value,
        "created_at": automation.created_at.isoformat(),
        "updated_at": automation.updated_at.isoformat(),
        "revision": automation.revision,
        "last_evaluated_at": (
            None
            if automation.last_evaluated_at is None
            else automation.last_evaluated_at.isoformat()
        ),
        "next_evaluation_at": (
            None
            if automation.next_evaluation_at is None
            else automation.next_evaluation_at.isoformat()
        ),
    }


def _delivery_json(delivery: TriggerDelivery) -> dict[str, JsonValue]:
    return {
        "id": delivery.id,
        "automation_id": delivery.automation_id,
        "trigger_type": delivery.trigger_type.value,
        "source": delivery.source,
        "dedupe_key": delivery.dedupe_key,
        "fired_at": delivery.fired_at.isoformat(),
        "payload": dict(delivery.payload),
        "status": delivery.status.value,
        "received_at": delivery.received_at.isoformat(),
        "attempt": delivery.attempt,
        "generated_task_id": delivery.generated_task_id,
        "error_code": delivery.error_code,
        "error_message": delivery.error_message,
        "processing_duration_ms": delivery.processing_duration_ms,
    }


def _object_json(encoded: str) -> dict[str, Any]:
    try:
        value: object = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION, "stored automation JSON is invalid"
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "stored automation JSON is not an object")
    return cast(dict[str, Any], value)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("stored timestamp must be a string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored timestamp must be timezone-aware")
    return parsed


def _optional_time(value: object) -> datetime | None:
    return None if value is None else _time(value)


def _automation_from_json(encoded: str) -> Automation:
    raw = _object_json(encoded)
    try:
        identity_raw = cast(dict[str, Any], raw["identity"])
        trigger_raw = cast(dict[str, Any], raw["trigger"])
        template_raw = cast(dict[str, Any], raw["task_template"])
        retry_raw = cast(dict[str, Any], raw["retry_policy"])
        trigger = TriggerDefinition(
            type=TriggerType(cast(str, trigger_raw["type"])),
            timezone=cast(str, trigger_raw["timezone"]),
            at=_optional_time(trigger_raw.get("at")),
            interval_seconds=cast(float | None, trigger_raw.get("interval_seconds")),
            event_type=cast(str | None, trigger_raw.get("event_type")),
            filters=cast(dict[str, JsonValue], trigger_raw.get("filters", {})),
            webhook_source=cast(str | None, trigger_raw.get("webhook_source")),
            verification_ref=cast(str | None, trigger_raw.get("verification_ref")),
            missed_schedule_policy=MissedSchedulePolicy(
                cast(str, trigger_raw.get("missed_schedule_policy", "coalesce"))
            ),
        )
        return Automation(
            id=cast(str, raw["id"]),
            name=cast(str, raw["name"]),
            description=cast(str, raw["description"]),
            identity=IdentityContext(
                principal_ref=cast(str, identity_raw["principal_ref"]),
                owner_type=cast(str, identity_raw["owner_type"]),
                owner_id=cast(str, identity_raw["owner_id"]),
            ),
            trigger=trigger,
            task_template=TaskTemplate(
                title=cast(str, template_raw["title"]),
                objective=cast(str, template_raw["objective"]),
                project_id=cast(str | None, template_raw.get("project_id")),
                workspace_id=cast(str | None, template_raw.get("workspace_id")),
                payload=cast(dict[str, JsonValue], template_raw.get("payload", {})),
            ),
            project_id=cast(str | None, raw.get("project_id")),
            workspace_id=cast(str | None, raw.get("workspace_id")),
            state=AutomationState(cast(str, raw["state"])),
            deduplication_strategy=cast(str, raw["deduplication_strategy"]),
            retry_policy=RetryPolicy(
                max_attempts=cast(int, retry_raw["max_attempts"]),
                base_backoff_seconds=cast(float, retry_raw["base_backoff_seconds"]),
            ),
            overlap_policy=OverlapPolicy(cast(str, raw["overlap_policy"])),
            created_at=_time(raw["created_at"]),
            updated_at=_time(raw["updated_at"]),
            revision=cast(int, raw["revision"]),
            last_evaluated_at=_optional_time(raw.get("last_evaluated_at")),
            next_evaluation_at=_optional_time(raw.get("next_evaluation_at")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION, "stored automation record is invalid"
        ) from exc


def _delivery_from_json(encoded: str) -> TriggerDelivery:
    raw = _object_json(encoded)
    try:
        return TriggerDelivery(
            id=cast(str, raw["id"]),
            automation_id=cast(str, raw["automation_id"]),
            trigger_type=TriggerType(cast(str, raw["trigger_type"])),
            source=cast(str, raw["source"]),
            dedupe_key=cast(str, raw["dedupe_key"]),
            fired_at=_time(raw["fired_at"]),
            payload=cast(dict[str, JsonValue], raw.get("payload", {})),
            status=DeliveryStatus(cast(str, raw["status"])),
            received_at=_time(raw["received_at"]),
            attempt=cast(int, raw["attempt"]),
            generated_task_id=cast(str | None, raw.get("generated_task_id")),
            error_code=cast(str | None, raw.get("error_code")),
            error_message=cast(str | None, raw.get("error_message")),
            processing_duration_ms=cast(float | None, raw.get("processing_duration_ms")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION, "stored trigger delivery record is invalid"
        ) from exc
