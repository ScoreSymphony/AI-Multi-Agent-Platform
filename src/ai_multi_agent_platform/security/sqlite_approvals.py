"""Durable approval and authorization-audit storage for shipped deployments."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.contracts import AuthorizationOutcome
from ai_multi_agent_platform.domain import Approval, ApprovalStatus, OwnerRef

from .approvals import ApprovalRecord, ApprovalService
from .authorization import (
    ActorType,
    AuthorizationAction,
    AuthorizationAuditRecord,
    ProposedAction,
    ResourceType,
    RiskClassification,
)


class SqliteApprovalService(ApprovalService):
    """Approval lifecycle whose exact-action bindings survive process restarts."""

    def __init__(
        self,
        database_path: Path,
        *,
        default_lifetime: timedelta = timedelta(minutes=15),
    ) -> None:
        super().__init__(default_lifetime=default_lifetime)
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._load()

    def request(
        self,
        action: ProposedAction,
        *,
        reason: str,
        policy_id: str,
        risk: RiskClassification = RiskClassification.ELEVATED,
        expires_at: datetime | None = None,
    ) -> ApprovalRecord:
        record = super().request(
            action,
            reason=reason,
            policy_id=policy_id,
            risk=risk,
            expires_at=expires_at,
        )
        self._persist(record)
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        record = super().get(approval_id)
        self._persist(record)
        return record

    def _decide_authorized(
        self,
        approval_id: str,
        *,
        approver_ref: str,
        approve: bool,
        comment: str | None = None,
    ) -> ApprovalRecord:
        record = super()._decide_authorized(
            approval_id,
            approver_ref=approver_ref,
            approve=approve,
            comment=comment,
        )
        self._persist(record)
        return record

    def _cancel_authorized(self, approval_id: str, *, actor_ref: str) -> ApprovalRecord:
        record = super()._cancel_authorized(approval_id, actor_ref=actor_ref)
        self._persist(record)
        return record

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def _load(self) -> None:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM approvals ORDER BY approval_id"
            ).fetchall()
        for row in rows:
            record = _approval_record_from_json(str(row["payload_json"]))
            self._records[record.approval_id] = record

    def _persist(self, record: ApprovalRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO approvals (approval_id, payload_json)
                VALUES (?, ?)
                ON CONFLICT(approval_id) DO UPDATE SET payload_json = excluded.payload_json
                """,
                (record.approval_id, _approval_record_to_json(record)),
            )


class SqliteAuthorizationAuditSink:
    """Append-only durable sink for value-free canonical authorization decisions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS authorization_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def __call__(self, record: AuthorizationAuditRecord) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO authorization_audit (payload_json) VALUES (?)",
                (_audit_record_to_json(record),),
            )

    def all(self) -> tuple[AuthorizationAuditRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM authorization_audit ORDER BY sequence"
            ).fetchall()
        return tuple(_audit_record_from_json(str(row["payload_json"])) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


def _owner_to_json(owner: OwnerRef | None) -> dict[str, str] | None:
    if owner is None:
        return None
    return {"type": owner.type, "id": owner.id}


def _owner_from_json(value: object) -> OwnerRef | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("invalid persisted approval owner")
    return OwnerRef(type=str(value["type"]), id=str(value["id"]))  # type: ignore[arg-type]


def _approval_record_to_json(record: ApprovalRecord) -> str:
    approval = record.approval
    payload = {
        "approval": {
            "id": approval.id,
            "subject_type": approval.subject_type,
            "subject_id": approval.subject_id,
            "owner_ref": _owner_to_json(approval.owner_ref),
            "status": approval.status.value,
            "reason": approval.reason,
            "decision_by": _owner_to_json(approval.decision_by),
            "project_id": approval.project_id,
            "created_at": approval.created_at.isoformat(),
            "updated_at": approval.updated_at.isoformat(),
        },
        "requester_ref": record.requester_ref,
        "action": record.action,
        "resource_type": record.resource_type,
        "resource_id": record.resource_id,
        "requested_action_digest": record.requested_action_digest,
        "risk": record.risk.value,
        "policy_id": record.policy_id,
        "expires_at": record.expires_at.isoformat(),
        "task_id": record.task_id,
        "run_id": record.run_id,
        "capability_ref": record.capability_ref,
        "payload_ref": record.payload_ref,
        "decision_at": record.decision_at.isoformat() if record.decision_at else None,
        "decision_comment": record.decision_comment,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _approval_record_from_json(encoded: str) -> ApprovalRecord:
    payload = json.loads(encoded)
    approval_data = payload["approval"]
    owner = _owner_from_json(approval_data["owner_ref"])
    if owner is None:
        raise ValueError("persisted approval is missing owner_ref")
    approval = Approval(
        id=approval_data["id"],
        subject_type=approval_data["subject_type"],
        subject_id=approval_data["subject_id"],
        owner_ref=owner,
        status=ApprovalStatus(approval_data["status"]),
        reason=approval_data["reason"],
        decision_by=_owner_from_json(approval_data.get("decision_by")),
        project_id=approval_data.get("project_id"),
        created_at=datetime.fromisoformat(approval_data["created_at"]),
        updated_at=datetime.fromisoformat(approval_data["updated_at"]),
    )
    decision_at = payload.get("decision_at")
    return ApprovalRecord(
        approval=approval,
        requester_ref=payload["requester_ref"],
        action=payload["action"],
        resource_type=payload["resource_type"],
        resource_id=payload["resource_id"],
        requested_action_digest=payload["requested_action_digest"],
        risk=RiskClassification(payload["risk"]),
        policy_id=payload["policy_id"],
        expires_at=datetime.fromisoformat(payload["expires_at"]),
        task_id=payload.get("task_id"),
        run_id=payload.get("run_id"),
        capability_ref=payload.get("capability_ref"),
        payload_ref=payload.get("payload_ref"),
        decision_at=datetime.fromisoformat(decision_at) if decision_at else None,
        decision_comment=payload.get("decision_comment"),
    )


def _audit_record_to_json(record: AuthorizationAuditRecord) -> str:
    return json.dumps(
        {
            "actor_ref": record.actor_ref,
            "actor_type": record.actor_type.value,
            "action": record.action.value,
            "resource_type": record.resource_type.value,
            "resource_id": record.resource_id,
            "outcome": record.outcome.value,
            "reason": record.reason,
            "policy_id": record.policy_id,
            "occurred_at": record.occurred_at.isoformat(),
            "correlation_id": record.correlation_id,
            "project_id": record.project_id,
            "task_id": record.task_id,
            "run_id": record.run_id,
            "approval_id": record.approval_id,
            "requested_action_digest": record.requested_action_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _audit_record_from_json(encoded: str) -> AuthorizationAuditRecord:
    payload = json.loads(encoded)
    return AuthorizationAuditRecord(
        actor_ref=payload["actor_ref"],
        actor_type=ActorType(payload["actor_type"]),
        action=AuthorizationAction(payload["action"]),
        resource_type=ResourceType(payload["resource_type"]),
        resource_id=payload["resource_id"],
        outcome=AuthorizationOutcome(payload["outcome"]),
        reason=payload.get("reason"),
        policy_id=payload.get("policy_id"),
        occurred_at=datetime.fromisoformat(payload["occurred_at"]),
        correlation_id=payload["correlation_id"],
        project_id=payload.get("project_id"),
        task_id=payload.get("task_id"),
        run_id=payload.get("run_id"),
        approval_id=payload.get("approval_id"),
        requested_action_digest=payload.get("requested_action_digest"),
    )
