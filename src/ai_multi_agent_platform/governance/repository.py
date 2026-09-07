"""Durable repository contracts and SQLite baseline for issue #501 governance state."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, validate_id
from ai_multi_agent_platform.security import RiskClassification

from .models import (
    ConversionStatus,
    GovernanceAuditEvent,
    Proposal,
    ProposalStatus,
    SpecificationRevision,
    TaskConversion,
)


@runtime_checkable
class GovernanceRepository(Protocol):
    def create_proposal(self, proposal: Proposal) -> Proposal: ...
    def revise_proposal(self, proposal: Proposal, *, expected_revision: int) -> Proposal: ...
    def get_proposal(self, proposal_id: str, revision: int | None = None) -> Proposal: ...
    def list_proposals(self) -> tuple[Proposal, ...]: ...
    def proposal_history(self, proposal_id: str) -> tuple[Proposal, ...]: ...
    def create_specification(self, specification: SpecificationRevision) -> SpecificationRevision: ...
    def revise_specification(
        self, specification: SpecificationRevision, *, expected_revision: int
    ) -> SpecificationRevision: ...
    def get_specification(
        self, specification_id: str, revision: int | None = None
    ) -> SpecificationRevision: ...
    def list_specifications(self) -> tuple[SpecificationRevision, ...]: ...
    def specification_history(self, specification_id: str) -> tuple[SpecificationRevision, ...]: ...
    def reserve_conversion(self, conversion: TaskConversion) -> TaskConversion: ...
    def complete_conversion(
        self, specification_id: str, *, approval_id: str | None
    ) -> TaskConversion: ...
    def get_conversion(self, specification_id: str) -> TaskConversion | None: ...
    def append_audit(self, event: GovernanceAuditEvent) -> None: ...
    def list_audit(self) -> tuple[GovernanceAuditEvent, ...]: ...


class SqliteGovernanceRepository(GovernanceRepository):
    """Correctness-first durable store with optimistic revision checks and unique conversion."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS governance_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_proposal_revisions (
                    proposal_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (proposal_id, revision)
                );
                CREATE TABLE IF NOT EXISTS governance_specifications (
                    specification_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS governance_specification_revisions (
                    specification_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (specification_id, revision)
                );
                CREATE TABLE IF NOT EXISTS governance_conversions (
                    specification_id TEXT PRIMARY KEY,
                    specification_revision INTEGER NOT NULL,
                    specification_digest TEXT NOT NULL,
                    proposal_id TEXT,
                    task_id TEXT NOT NULL UNIQUE,
                    approval_id TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS governance_audit_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                """
            )

    def create_proposal(self, proposal: Proposal) -> Proposal:
        payload = _dump(_proposal_to_json(proposal))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO governance_proposals(proposal_id, revision, payload_json) "
                    "VALUES (?, ?, ?)",
                    (proposal.id, proposal.revision, payload),
                )
                connection.execute(
                    "INSERT INTO governance_proposal_revisions(proposal_id, revision, payload_json) "
                    "VALUES (?, ?, ?)",
                    (proposal.id, proposal.revision, payload),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(ErrorCode.CONFLICT, "proposal already exists") from exc
        return proposal

    def revise_proposal(self, proposal: Proposal, *, expected_revision: int) -> Proposal:
        if proposal.revision != expected_revision + 1:
            raise ValueError("proposal revision must increment expected_revision exactly once")
        payload = _dump(_proposal_to_json(proposal))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM governance_proposals WHERE proposal_id = ?",
                (proposal.id,),
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, "proposal was not found")
            if int(row["revision"]) != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "proposal revision conflict",
                    details={"expected_revision": expected_revision, "actual_revision": int(row["revision"])},
                )
            connection.execute(
                "INSERT INTO governance_proposal_revisions(proposal_id, revision, payload_json) "
                "VALUES (?, ?, ?)",
                (proposal.id, proposal.revision, payload),
            )
            connection.execute(
                "UPDATE governance_proposals SET revision = ?, payload_json = ? WHERE proposal_id = ?",
                (proposal.revision, payload, proposal.id),
            )
        return proposal

    def get_proposal(self, proposal_id: str, revision: int | None = None) -> Proposal:
        validate_id(proposal_id, "proposal")
        with self._connect() as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT payload_json FROM governance_proposals WHERE proposal_id = ?",
                    (proposal_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload_json FROM governance_proposal_revisions "
                    "WHERE proposal_id = ? AND revision = ?",
                    (proposal_id, revision),
                ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "proposal was not found")
        return _proposal_from_json(_load(str(row["payload_json"])))

    def list_proposals(self) -> tuple[Proposal, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM governance_proposals ORDER BY proposal_id"
            ).fetchall()
        return tuple(_proposal_from_json(_load(str(row["payload_json"]))) for row in rows)

    def proposal_history(self, proposal_id: str) -> tuple[Proposal, ...]:
        validate_id(proposal_id, "proposal")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM governance_proposal_revisions "
                "WHERE proposal_id = ? ORDER BY revision",
                (proposal_id,),
            ).fetchall()
        if not rows:
            raise ContractError(ErrorCode.NOT_FOUND, "proposal was not found")
        return tuple(_proposal_from_json(_load(str(row["payload_json"]))) for row in rows)

    def create_specification(self, specification: SpecificationRevision) -> SpecificationRevision:
        payload = _dump(_specification_to_json(specification))
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO governance_specifications"
                    "(specification_id, revision, digest, payload_json) VALUES (?, ?, ?, ?)",
                    (specification.id, specification.revision, specification.content_digest, payload),
                )
                connection.execute(
                    "INSERT INTO governance_specification_revisions"
                    "(specification_id, revision, digest, payload_json) VALUES (?, ?, ?, ?)",
                    (specification.id, specification.revision, specification.content_digest, payload),
                )
        except sqlite3.IntegrityError as exc:
            raise ContractError(ErrorCode.CONFLICT, "specification already exists") from exc
        return specification

    def revise_specification(
        self, specification: SpecificationRevision, *, expected_revision: int
    ) -> SpecificationRevision:
        if specification.revision != expected_revision + 1:
            raise ValueError("specification revision must increment expected_revision exactly once")
        payload = _dump(_specification_to_json(specification))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM governance_specifications WHERE specification_id = ?",
                (specification.id,),
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, "specification was not found")
            if int(row["revision"]) != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "specification revision conflict",
                    details={"expected_revision": expected_revision, "actual_revision": int(row["revision"])},
                )
            if connection.execute(
                "SELECT 1 FROM governance_conversions WHERE specification_id = ?",
                (specification.id,),
            ).fetchone() is not None:
                raise ContractError(ErrorCode.CONFLICT, "converted specification cannot be revised")
            connection.execute(
                "INSERT INTO governance_specification_revisions"
                "(specification_id, revision, digest, payload_json) VALUES (?, ?, ?, ?)",
                (specification.id, specification.revision, specification.content_digest, payload),
            )
            connection.execute(
                "UPDATE governance_specifications SET revision = ?, digest = ?, payload_json = ? "
                "WHERE specification_id = ?",
                (
                    specification.revision,
                    specification.content_digest,
                    payload,
                    specification.id,
                ),
            )
        return specification

    def get_specification(
        self, specification_id: str, revision: int | None = None
    ) -> SpecificationRevision:
        validate_id(specification_id, "specification")
        with self._connect() as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT payload_json FROM governance_specifications WHERE specification_id = ?",
                    (specification_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT payload_json FROM governance_specification_revisions "
                    "WHERE specification_id = ? AND revision = ?",
                    (specification_id, revision),
                ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "specification was not found")
        return _specification_from_json(_load(str(row["payload_json"])))

    def list_specifications(self) -> tuple[SpecificationRevision, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM governance_specifications ORDER BY specification_id"
            ).fetchall()
        return tuple(_specification_from_json(_load(str(row["payload_json"]))) for row in rows)

    def specification_history(self, specification_id: str) -> tuple[SpecificationRevision, ...]:
        validate_id(specification_id, "specification")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM governance_specification_revisions "
                "WHERE specification_id = ? ORDER BY revision",
                (specification_id,),
            ).fetchall()
        if not rows:
            raise ContractError(ErrorCode.NOT_FOUND, "specification was not found")
        return tuple(_specification_from_json(_load(str(row["payload_json"]))) for row in rows)

    def reserve_conversion(self, conversion: TaskConversion) -> TaskConversion:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM governance_conversions WHERE specification_id = ?",
                (conversion.specification_id,),
            ).fetchone()
            if row is not None:
                existing = _conversion_from_row(row)
                if (
                    existing.specification_revision != conversion.specification_revision
                    or existing.specification_digest != conversion.specification_digest
                ):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "specification already has a conversion for another revision",
                    )
                return existing
            connection.execute(
                "INSERT INTO governance_conversions"
                "(specification_id, specification_revision, specification_digest, proposal_id, "
                "task_id, approval_id, status, created_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversion.specification_id,
                    conversion.specification_revision,
                    conversion.specification_digest,
                    conversion.proposal_id,
                    conversion.task_id,
                    conversion.approval_id,
                    conversion.status.value,
                    conversion.created_at.isoformat(),
                    None,
                ),
            )
        return conversion

    def complete_conversion(
        self, specification_id: str, *, approval_id: str | None
    ) -> TaskConversion:
        validate_id(specification_id, "specification")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM governance_conversions WHERE specification_id = ?",
                (specification_id,),
            ).fetchone()
            if row is None:
                raise ContractError(ErrorCode.NOT_FOUND, "conversion was not reserved")
            current = _conversion_from_row(row)
            if current.status is ConversionStatus.COMPLETED:
                return current
            completed_at = datetime.now(current.created_at.tzinfo).isoformat()
            connection.execute(
                "UPDATE governance_conversions SET status = ?, approval_id = COALESCE(?, approval_id), "
                "completed_at = ? WHERE specification_id = ?",
                (ConversionStatus.COMPLETED.value, approval_id, completed_at, specification_id),
            )
            updated = connection.execute(
                "SELECT * FROM governance_conversions WHERE specification_id = ?",
                (specification_id,),
            ).fetchone()
        if updated is None:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "conversion disappeared after update")
        return _conversion_from_row(updated)

    def get_conversion(self, specification_id: str) -> TaskConversion | None:
        validate_id(specification_id, "specification")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM governance_conversions WHERE specification_id = ?",
                (specification_id,),
            ).fetchone()
        return None if row is None else _conversion_from_row(row)

    def append_audit(self, event: GovernanceAuditEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO governance_audit_events(event_id, occurred_at, payload_json) "
                "VALUES (?, ?, ?)",
                (event.id, event.occurred_at.isoformat(), _dump(_audit_to_json(event))),
            )

    def list_audit(self) -> tuple[GovernanceAuditEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload_json FROM governance_audit_events ORDER BY occurred_at, event_id"
            ).fetchall()
        return tuple(_audit_from_json(_load(str(row["payload_json"]))) for row in rows)


def _proposal_to_json(value: Proposal) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "title": value.title,
        "summary": value.summary,
        "reason": value.reason,
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "requester_ref": value.requester_ref,
        "source": value.source,
        "status": value.status.value,
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "evidence_refs": list(value.evidence_refs),
        "confidence": value.confidence,
        "expected_value": value.expected_value,
        "risk": value.risk.value,
        "fingerprint": value.fingerprint,
        "supersedes_id": value.supersedes_id,
        "superseded_by_id": value.superseded_by_id,
        "expires_at": value.expires_at.isoformat() if value.expires_at else None,
        "revision": value.revision,
        "provenance": dict(value.provenance),
        "converted_task_id": value.converted_task_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
        "schema_version": value.schema_version,
    }


def _proposal_from_json(raw: Mapping[str, object]) -> Proposal:
    owner = _mapping(raw, "owner_ref")
    return Proposal(
        id=_string(raw, "id"),
        title=_string(raw, "title"),
        summary=_string(raw, "summary"),
        reason=_string(raw, "reason"),
        owner_ref=OwnerRef(type=cast(object, owner["type"]), id=str(owner["id"])),  # type: ignore[arg-type]
        requester_ref=_string(raw, "requester_ref"),
        source=_string(raw, "source"),
        status=ProposalStatus(_string(raw, "status")),
        project_id=_optional_string(raw, "project_id"),
        workspace_id=_optional_string(raw, "workspace_id"),
        evidence_refs=_strings(raw, "evidence_refs"),
        confidence=_optional_float(raw, "confidence"),
        expected_value=_optional_float(raw, "expected_value"),
        risk=RiskClassification(_string(raw, "risk")),
        fingerprint=_optional_string(raw, "fingerprint"),
        supersedes_id=_optional_string(raw, "supersedes_id"),
        superseded_by_id=_optional_string(raw, "superseded_by_id"),
        expires_at=_optional_datetime(raw, "expires_at"),
        revision=_int(raw, "revision"),
        provenance=cast(Mapping[str, JsonValue], _mapping(raw, "provenance")),
        converted_task_id=_optional_string(raw, "converted_task_id"),
        created_at=_datetime(raw, "created_at"),
        updated_at=_datetime(raw, "updated_at"),
        schema_version=_string(raw, "schema_version"),
    )


def _specification_to_json(value: SpecificationRevision) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "revision": value.revision,
        "proposal_id": value.proposal_id,
        "goal_id": value.goal_id,
        "task_intake_id": value.task_intake_id,
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "problem": value.problem,
        "goal": value.goal,
        "scope": list(value.scope),
        "out_of_scope": list(value.out_of_scope),
        "acceptance_criteria": list(value.acceptance_criteria),
        "dependencies": list(value.dependencies),
        "constraints": list(value.constraints),
        "risk": value.risk.value,
        "required_capabilities": list(value.required_capabilities),
        "model_requirements": dict(value.model_requirements),
        "agent_requirements": dict(value.agent_requirements),
        "data_security_constraints": list(value.data_security_constraints),
        "validation_strategy": list(value.validation_strategy),
        "required_tests": list(value.required_tests),
        "verification_requirements": list(value.verification_requirements),
        "required_human_gates": list(value.required_human_gates),
        "decomposition_hints": list(value.decomposition_hints),
        "assumptions": list(value.assumptions),
        "open_questions": list(value.open_questions),
        "owner_ref": {"type": value.owner_ref.type, "id": value.owner_ref.id},
        "requester_ref": value.requester_ref,
        "provenance": dict(value.provenance),
        "content_digest": value.content_digest,
        "created_at": value.created_at.isoformat(),
        "schema_version": value.schema_version,
    }


def _specification_from_json(raw: Mapping[str, object]) -> SpecificationRevision:
    owner = _mapping(raw, "owner_ref")
    return SpecificationRevision(
        id=_string(raw, "id"),
        revision=_int(raw, "revision"),
        proposal_id=_optional_string(raw, "proposal_id"),
        goal_id=_optional_string(raw, "goal_id"),
        task_intake_id=_optional_string(raw, "task_intake_id"),
        project_id=_optional_string(raw, "project_id"),
        workspace_id=_optional_string(raw, "workspace_id"),
        problem=_string(raw, "problem"),
        goal=_string(raw, "goal"),
        scope=_strings(raw, "scope"),
        out_of_scope=_strings(raw, "out_of_scope"),
        acceptance_criteria=_strings(raw, "acceptance_criteria"),
        dependencies=_strings(raw, "dependencies"),
        constraints=_strings(raw, "constraints"),
        risk=RiskClassification(_string(raw, "risk")),
        required_capabilities=_strings(raw, "required_capabilities"),
        model_requirements=cast(Mapping[str, JsonValue], _mapping(raw, "model_requirements")),
        agent_requirements=cast(Mapping[str, JsonValue], _mapping(raw, "agent_requirements")),
        data_security_constraints=_strings(raw, "data_security_constraints"),
        validation_strategy=_strings(raw, "validation_strategy"),
        required_tests=_strings(raw, "required_tests"),
        verification_requirements=_strings(raw, "verification_requirements"),
        required_human_gates=_strings(raw, "required_human_gates"),
        decomposition_hints=_strings(raw, "decomposition_hints"),
        assumptions=_strings(raw, "assumptions"),
        open_questions=_strings(raw, "open_questions"),
        owner_ref=OwnerRef(type=cast(object, owner["type"]), id=str(owner["id"])),  # type: ignore[arg-type]
        requester_ref=_string(raw, "requester_ref"),
        provenance=cast(Mapping[str, JsonValue], _mapping(raw, "provenance")),
        content_digest=_string(raw, "content_digest"),
        created_at=_datetime(raw, "created_at"),
        schema_version=_string(raw, "schema_version"),
    )


def _conversion_from_row(row: sqlite3.Row) -> TaskConversion:
    return TaskConversion(
        specification_id=str(row["specification_id"]),
        specification_revision=int(row["specification_revision"]),
        specification_digest=str(row["specification_digest"]),
        proposal_id=str(row["proposal_id"]) if row["proposal_id"] is not None else None,
        task_id=str(row["task_id"]),
        approval_id=str(row["approval_id"]) if row["approval_id"] is not None else None,
        status=ConversionStatus(str(row["status"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        completed_at=(
            datetime.fromisoformat(str(row["completed_at"]))
            if row["completed_at"] is not None
            else None
        ),
    )


def _audit_to_json(value: GovernanceAuditEvent) -> dict[str, JsonValue]:
    return {
        "id": value.id,
        "event_type": value.event_type,
        "resource_type": value.resource_type,
        "resource_id": value.resource_id,
        "actor_ref": value.actor_ref,
        "project_id": value.project_id,
        "revision": value.revision,
        "digest": value.digest,
        "metadata": dict(value.metadata),
        "occurred_at": value.occurred_at.isoformat(),
    }


def _audit_from_json(raw: Mapping[str, object]) -> GovernanceAuditEvent:
    resource_type = _string(raw, "resource_type")
    if resource_type not in {"proposal", "specification", "conversion"}:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "invalid governance audit resource type")
    return GovernanceAuditEvent(
        id=_string(raw, "id"),
        event_type=_string(raw, "event_type"),
        resource_type=cast("Literal['proposal', 'specification', 'conversion']", resource_type),
        resource_id=_string(raw, "resource_id"),
        actor_ref=_string(raw, "actor_ref"),
        project_id=_optional_string(raw, "project_id"),
        revision=_optional_int(raw, "revision"),
        digest=_optional_string(raw, "digest"),
        metadata=cast(Mapping[str, JsonValue], _mapping(raw, "metadata")),
        occurred_at=_datetime(raw, "occurred_at"),
    )


def _dump(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _load(value: str) -> Mapping[str, object]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, "governance payload must be an object")
    return cast(Mapping[str, object], parsed)


def _string(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value:
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return value


def _optional_string(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return value


def _mapping(raw: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return cast(Mapping[str, object], value)


def _strings(raw: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = raw.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return tuple(cast(list[str], value))


def _int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return value


def _optional_int(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return value


def _optional_float(raw: Mapping[str, object], key: str) -> float | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ContractError(ErrorCode.CONTRACT_VIOLATION, f"invalid governance field: {key}")
    return float(value)


def _datetime(raw: Mapping[str, object], key: str) -> datetime:
    return datetime.fromisoformat(_string(raw, key))


def _optional_datetime(raw: Mapping[str, object], key: str) -> datetime | None:
    value = _optional_string(raw, key)
    return None if value is None else datetime.fromisoformat(value)
