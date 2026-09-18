"""SQLite persistence for durable external-effect recovery evidence."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue

from .recovery import (
    ExternalEffectRecoveryDisposition,
    ExternalEffectRecoveryRecord,
    ExternalEffectRecoveryStatus,
)
from .types import (
    ExternalEffectIdempotency,
    ExternalEffectReconciliationSupport,
    SideEffectClassification,
)


class SQLiteExternalEffectRecoveryRepository:
    """Restart-safe journal that deliberately does not persist invocation arguments."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS external_effect_recovery (
                    effect_id TEXT PRIMARY KEY,
                    invocation_id TEXT NOT NULL UNIQUE,
                    provider_id TEXT NOT NULL,
                    capability_id TEXT NOT NULL,
                    idempotency_key TEXT,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS external_effect_recovery_idempotency
                ON external_effect_recovery(provider_id, capability_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, effect_id: str) -> ExternalEffectRecoveryRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM external_effect_recovery WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        if row is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                "external effect recovery record not found",
            )
        return _record_from_json(_load(row[0]))

    def find_by_invocation(self, invocation_id: str) -> ExternalEffectRecoveryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM external_effect_recovery WHERE invocation_id = ?",
                (invocation_id,),
            ).fetchone()
        return None if row is None else _record_from_json(_load(row[0]))

    def find_by_idempotency(
        self,
        provider_id: str,
        capability_id: str,
        idempotency_key: str,
    ) -> ExternalEffectRecoveryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload FROM external_effect_recovery
                WHERE provider_id = ? AND capability_id = ? AND idempotency_key = ?
                """,
                (provider_id, capability_id, idempotency_key),
            ).fetchone()
        return None if row is None else _record_from_json(_load(row[0]))

    def list(self) -> tuple[ExternalEffectRecoveryRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM external_effect_recovery ORDER BY effect_id"
            ).fetchall()
        return tuple(_record_from_json(_load(row[0])) for row in rows)

    def save(self, record: ExternalEffectRecoveryRecord) -> ExternalEffectRecoveryRecord:
        payload = json.dumps(_record_to_json(record), sort_keys=True, separators=(",", ":"))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO external_effect_recovery(
                        effect_id, invocation_id, provider_id, capability_id, idempotency_key, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(effect_id) DO UPDATE SET
                        invocation_id = excluded.invocation_id,
                        provider_id = excluded.provider_id,
                        capability_id = excluded.capability_id,
                        idempotency_key = excluded.idempotency_key,
                        payload = excluded.payload
                    """,
                    (
                        record.effect_id,
                        record.invocation_id,
                        record.provider_id,
                        record.capability_id,
                        record.idempotency_key,
                        payload,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(
                ErrorCode.CONFLICT,
                "external effect recovery identity/idempotency conflict",
            ) from exc
        return record


def _load(value: str) -> dict[str, JsonValue]:
    raw = json.loads(value)
    if not isinstance(raw, dict):
        raise ContractError(
            ErrorCode.BACKEND_ERROR,
            "external effect recovery payload must be an object",
        )
    return cast(dict[str, JsonValue], raw)


def _metadata_to_json(metadata: tuple[AdapterMetadata, ...]) -> JsonValue:
    return [
        {"namespace": item.namespace, "values": dict(item.values)}
        for item in metadata
    ]


def _metadata_from_json(value: JsonValue) -> tuple[AdapterMetadata, ...]:
    if not isinstance(value, list):
        return ()
    items: list[AdapterMetadata] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        namespace = item.get("namespace")
        values = item.get("values")
        if isinstance(namespace, str) and isinstance(values, dict):
            items.append(
                AdapterMetadata(
                    namespace=namespace,
                    values=cast(dict[str, JsonValue], values),
                )
            )
    return tuple(items)


def _record_to_json(record: ExternalEffectRecoveryRecord) -> dict[str, JsonValue]:
    return {
        "effect_id": record.effect_id,
        "invocation_id": record.invocation_id,
        "canonical_tool_invocation_id": record.canonical_tool_invocation_id,
        "task_id": record.task_id,
        "run_id": record.run_id,
        "capability_id": record.capability_id,
        "capability_version": record.capability_version,
        "provider_id": record.provider_id,
        "provider_tool_ref": record.provider_tool_ref,
        "side_effects": record.side_effects.value,
        "idempotency": record.idempotency.value,
        "reconciliation_support": record.reconciliation_support.value,
        "idempotency_key": record.idempotency_key,
        "status": record.status.value,
        "disposition": record.disposition.value,
        "reason": record.reason,
        "dispatch_attempts": record.dispatch_attempts,
        "reconciliation_attempts": record.reconciliation_attempts,
        "duplicate_callbacks_ignored": record.duplicate_callbacks_ignored,
        "result_ref": record.result_ref,
        "artifact_refs": list(record.artifact_refs),
        "evidence_refs": list(record.evidence_refs),
        "adapter_metadata": _metadata_to_json(record.adapter_metadata),
        "last_operator_actor": record.last_operator_actor,
        "last_operator_reason": record.last_operator_reason,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_json(data: dict[str, JsonValue]) -> ExternalEffectRecoveryRecord:
    return ExternalEffectRecoveryRecord(
        effect_id=cast(str, data["effect_id"]),
        invocation_id=cast(str, data["invocation_id"]),
        canonical_tool_invocation_id=cast(
            str | None,
            data.get("canonical_tool_invocation_id"),
        ),
        task_id=cast(str, data["task_id"]),
        run_id=cast(str, data["run_id"]),
        capability_id=cast(str, data["capability_id"]),
        capability_version=cast(str, data["capability_version"]),
        provider_id=cast(str, data["provider_id"]),
        provider_tool_ref=cast(str, data["provider_tool_ref"]),
        side_effects=SideEffectClassification(cast(str, data["side_effects"])),
        idempotency=ExternalEffectIdempotency(cast(str, data["idempotency"])),
        reconciliation_support=ExternalEffectReconciliationSupport(
            cast(str, data["reconciliation_support"])
        ),
        idempotency_key=cast(str | None, data.get("idempotency_key")),
        status=ExternalEffectRecoveryStatus(cast(str, data["status"])),
        disposition=ExternalEffectRecoveryDisposition(cast(str, data["disposition"])),
        reason=cast(str, data["reason"]),
        dispatch_attempts=int(cast(int, data.get("dispatch_attempts", 1))),
        reconciliation_attempts=int(cast(int, data.get("reconciliation_attempts", 0))),
        duplicate_callbacks_ignored=int(
            cast(int, data.get("duplicate_callbacks_ignored", 0))
        ),
        result_ref=cast(str | None, data.get("result_ref")),
        artifact_refs=tuple(cast(list[str], data.get("artifact_refs", []))),
        evidence_refs=tuple(cast(list[str], data.get("evidence_refs", []))),
        adapter_metadata=_metadata_from_json(data.get("adapter_metadata", [])),
        last_operator_actor=cast(str | None, data.get("last_operator_actor")),
        last_operator_reason=cast(str | None, data.get("last_operator_reason")),
        created_at=datetime.fromisoformat(cast(str, data["created_at"])),
        updated_at=datetime.fromisoformat(cast(str, data["updated_at"])),
    )
