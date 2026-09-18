"""Durable post-success audit and observability for managed Applications."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane.extensions import ControlPlane, ControlPlaneModule, ResourceService
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.control_plane.module_registry import install_control_plane_modules
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

APPLICATION_AUDIT_COLLECTION = "application-audit-events"
APPLICATION_AUDIT_MODULE = "applications.audit"
APPLICATION_AUDIT_TYPE = "application-audit-event"
AUDITED_APPLICATION_COMMANDS = frozenset(
    {
        "application.install",
        "application.configure",
        "application.start",
        "application.stop",
        "application.restart",
        "application.remove",
        "application.reconcile",
    }
)


class SqliteApplicationAuditStore:
    """Durable value-free audit projection sharing the Application SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS application_audit_events (
                    event_id TEXT PRIMARY KEY,
                    application_id TEXT NOT NULL,
                    instance_id TEXT,
                    command TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_application_audit_application
                    ON application_audit_events(application_id, occurred_at, event_id);

                CREATE INDEX IF NOT EXISTS idx_application_audit_instance
                    ON application_audit_events(instance_id, occurred_at, event_id);
                """
            )

    def append(self, resource: Mapping[str, JsonValue]) -> None:
        event_id = _required_string(resource, "id")
        application_id = _required_string(resource, "application_id")
        command = _required_string(resource, "command")
        occurred_at = _required_string(resource, "occurred_at")
        instance_value = resource.get("instance_id")
        instance_id = instance_value if isinstance(instance_value, str) else None
        payload = json.dumps(
            dict(resource),
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO application_audit_events (
                    event_id,
                    application_id,
                    instance_id,
                    command,
                    occurred_at,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    application_id,
                    instance_id,
                    command,
                    occurred_at,
                    payload,
                ),
            )

    def get(self, event_id: str) -> dict[str, JsonValue]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM application_audit_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"application audit event not found: {event_id}",
            )
        return _decode_resource(str(row["payload_json"]))

    def list(
        self,
        *,
        application_id: str | None = None,
        instance_id: str | None = None,
        command: str | None = None,
    ) -> tuple[dict[str, JsonValue], ...]:
        conditions: list[str] = []
        parameters: list[str] = []
        if application_id is not None:
            conditions.append("application_id = ?")
            parameters.append(application_id)
        if instance_id is not None:
            conditions.append("instance_id = ?")
            parameters.append(instance_id)
        if command is not None:
            conditions.append("command = ?")
            parameters.append(command)
        where = "" if not conditions else " WHERE " + " AND ".join(conditions)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM application_audit_events"
                + where
                + " ORDER BY occurred_at, event_id",
                parameters,
            ).fetchall()
        return tuple(_decode_resource(str(row["payload_json"])) for row in rows)


class ApplicationAuditLog:
    """Persist successful Application mutations and mirror them into telemetry."""

    def __init__(
        self,
        store: SqliteApplicationAuditStore,
        *,
        telemetry: Telemetry | None = None,
    ) -> None:
        self._store = store
        self._telemetry = telemetry

    async def record_command(
        self,
        context: RequestContext,
        command: str,
        resource_ref: str,
        result: dict[str, JsonValue],
    ) -> dict[str, JsonValue] | None:
        if command not in AUDITED_APPLICATION_COMMANDS:
            return None
        application_id = result.get("application_id")
        if not isinstance(application_id, str) or not application_id:
            return None
        instance_value = result.get("id")
        instance_id = (
            instance_value
            if isinstance(instance_value, str) and instance_value.startswith("application_instance_")
            else None
        )
        occurred_at = datetime.now(UTC)
        event = _audit_resource(
            event_id=_audit_event_id(context, command, resource_ref),
            command=command,
            resource_ref=resource_ref,
            application_id=application_id,
            instance_id=instance_id,
            actor_ref=context.actor.principal_ref,
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            idempotency_key=context.idempotency_key,
            occurred_at=occurred_at,
            result=result,
        )
        self._store.append(event)
        self._observe(event, occurred_at)
        return event

    def _observe(
        self,
        event: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> None:
        if self._telemetry is None:
            return
        application_id = _required_string(event, "application_id")
        instance_value = event.get("instance_id")
        instance_id = instance_value if isinstance(instance_value, str) else None
        correlation_id = _required_string(event, "correlation_id")
        command = _required_string(event, "command")
        context = TelemetryContext(
            correlation_id=correlation_id,
            adapter_id=application_id,
        )
        attributes: dict[str, JsonValue] = {
            "application_id": application_id,
            "command": command,
        }
        if instance_id is not None:
            attributes["instance_id"] = instance_id
        for field in ("runtime_id", "desired_state", "observed_state", "health"):
            value = event.get(field)
            if isinstance(value, str):
                attributes[field] = value
        self._telemetry.log(
            severity=TelemetrySeverity.INFO,
            component=FailureComponent.APPLICATION_ADAPTER,
            event_name=command,
            context=context,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes=attributes,
        )
        self._telemetry.metric(
            "platform.application.commands.succeeded",
            1.0,
            context=context,
            attributes={"command": command},
            timestamp=occurred_at,
        )
        self._telemetry.timeline(
            event_name=command,
            component=FailureComponent.APPLICATION_ADAPTER,
            context=context,
            timestamp=occurred_at,
            outcome=TelemetryOutcome.SUCCEEDED,
            attributes=attributes,
        )


class ApplicationAuditResourceService(ResourceService):
    search_indexable = False

    def __init__(self, store: SqliteApplicationAuditStore) -> None:
        self._store = store

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        filters = query.filters or {}
        return self._store.list(
            application_id=_optional_filter(filters, "application_id"),
            instance_id=_optional_filter(filters, "instance_id"),
            command=_optional_filter(filters, "command"),
        )

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        return self._store.get(resource_id)


def application_audit_control_plane_module(
    audit: ApplicationAuditLog,
    store: SqliteApplicationAuditStore,
) -> ControlPlaneModule:
    """Build the Application-owned post-success audit projection."""

    async def observe(
        context: RequestContext,
        command: str,
        resource_ref: str,
        result: dict[str, JsonValue],
    ) -> None:
        await audit.record_command(context, command, resource_ref, result)

    return ControlPlaneModule(
        name=APPLICATION_AUDIT_MODULE,
        requires=frozenset({"applications"}),
        resource_services={
            APPLICATION_AUDIT_COLLECTION: ApplicationAuditResourceService(store),
        },
        command_observers=(observe,),
    )


def register_application_audit_control_plane(
    control_plane: ControlPlane,
    audit: ApplicationAuditLog,
    store: SqliteApplicationAuditStore,
) -> None:
    """Register durable Application mutation history and success observability."""

    install_control_plane_modules(
        control_plane,
        (application_audit_control_plane_module(audit, store),),
    )


def _audit_event_id(context: RequestContext, command: str, resource_ref: str) -> str:
    logical_key = context.idempotency_key or context.request_id
    value = uuid5(
        NAMESPACE_URL,
        f"ai-multi-agent-platform:application-audit:{logical_key}:{command}:{resource_ref}",
    )
    return f"event_{value}"


def _audit_resource(
    *,
    event_id: str,
    command: str,
    resource_ref: str,
    application_id: str,
    instance_id: str | None,
    actor_ref: str,
    request_id: str,
    correlation_id: str,
    idempotency_key: str | None,
    occurred_at: datetime,
    result: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    resource: dict[str, JsonValue] = {
        "id": event_id,
        "type": APPLICATION_AUDIT_TYPE,
        "command": command,
        "resource_ref": resource_ref,
        "application_id": application_id,
        "instance_id": instance_id,
        "actor_ref": actor_ref,
        "request_id": request_id,
        "correlation_id": correlation_id,
        "idempotency_key": idempotency_key,
        "occurred_at": occurred_at.isoformat(),
    }
    for field in (
        "application_version",
        "runtime_id",
        "node_id",
        "desired_state",
        "observed_state",
        "health",
    ):
        value = result.get(field)
        if isinstance(value, str):
            resource[field] = value
    revision = result.get("revision")
    if isinstance(revision, int) and not isinstance(revision, bool):
        resource["revision"] = revision
    return resource


def _decode_resource(payload: str) -> dict[str, JsonValue]:
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise RuntimeError("stored application audit payload must be an object")
    return value


def _required_string(value: Mapping[str, JsonValue], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field} must be a non-blank string")
    return item


def _optional_filter(filters: Mapping[str, str], name: str) -> str | None:
    value = filters.get(name)
    return value if isinstance(value, str) and value else None


__all__ = [
    "APPLICATION_AUDIT_COLLECTION",
    "APPLICATION_AUDIT_MODULE",
    "APPLICATION_AUDIT_TYPE",
    "AUDITED_APPLICATION_COMMANDS",
    "ApplicationAuditLog",
    "ApplicationAuditResourceService",
    "SqliteApplicationAuditStore",
    "application_audit_control_plane_module",
    "register_application_audit_control_plane",
]
