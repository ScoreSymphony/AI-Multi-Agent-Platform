"""Planning proposal repositories, including durable restart-safe JSON storage."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.models import RoutingRequirements

from .models import (
    AgentAssignment,
    CapabilityRequirement,
    PlannerDescriptor,
    PlannerKind,
    PlanningStepDraft,
    PlanningTrigger,
    PlanProposal,
    ProposalRecord,
    ProposalStatus,
    ProposalValidation,
)

PLANNING_STORE_SCHEMA_VERSION = "1.0"


class PlanningRepository(Protocol):
    def create(self, record: ProposalRecord) -> ProposalRecord: ...

    def get(self, proposal_id: str) -> ProposalRecord: ...

    def save(self, record: ProposalRecord, *, expected_revision: int) -> ProposalRecord: ...

    def list_all(self) -> tuple[ProposalRecord, ...]: ...

    def list_for_task(self, task_id: str) -> tuple[ProposalRecord, ...]: ...

    def get_by_idempotency(self, task_id: str, key: str) -> ProposalRecord | None: ...

    def get_by_trigger(self, task_id: str, fingerprint: str) -> ProposalRecord | None: ...

    def pending_activation(self, task_id: str) -> ProposalRecord | None: ...


class InMemoryPlanningRepository:
    """Optimistic-revision repository for the zero-config local/reference path."""

    def __init__(self) -> None:
        self._records: dict[str, ProposalRecord] = {}
        self._lock = threading.RLock()

    def create(self, record: ProposalRecord) -> ProposalRecord:
        with self._lock:
            existing = self.get_by_idempotency(record.proposal.task_id, record.idempotency_key)
            if existing is not None:
                return existing
            duplicate_trigger = self.get_by_trigger(
                record.proposal.task_id,
                record.trigger_fingerprint,
            )
            if duplicate_trigger is not None:
                return duplicate_trigger
            if record.proposal.proposal_id in self._records:
                raise ContractError(ErrorCode.CONFLICT, "planning proposal already exists")
            self._records[record.proposal.proposal_id] = record
            return record

    def get(self, proposal_id: str) -> ProposalRecord:
        with self._lock:
            try:
                return self._records[proposal_id]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND,
                    f"planning proposal not found: {proposal_id}",
                ) from exc

    def save(self, record: ProposalRecord, *, expected_revision: int) -> ProposalRecord:
        with self._lock:
            current = self.get(record.proposal.proposal_id)
            if current.revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "planning proposal revision changed concurrently",
                    details={
                        "proposal_id": record.proposal.proposal_id,
                        "expected_revision": expected_revision,
                        "current_revision": current.revision,
                    },
                )
            if record.revision != expected_revision + 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "saved proposal record revision must increase exactly once",
                )
            self._records[record.proposal.proposal_id] = record
            return record

    def list_all(self) -> tuple[ProposalRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._records.values(),
                    key=lambda item: (item.proposal.created_at, item.proposal.proposal_id),
                )
            )

    def list_for_task(self, task_id: str) -> tuple[ProposalRecord, ...]:
        return tuple(record for record in self.list_all() if record.proposal.task_id == task_id)

    def get_by_idempotency(self, task_id: str, key: str) -> ProposalRecord | None:
        with self._lock:
            for record in self._records.values():
                if record.proposal.task_id == task_id and record.idempotency_key == key:
                    return record
            return None

    def get_by_trigger(self, task_id: str, fingerprint: str) -> ProposalRecord | None:
        with self._lock:
            for record in self._records.values():
                if (
                    record.proposal.task_id == task_id
                    and record.trigger_fingerprint == fingerprint
                    and record.status is not ProposalStatus.REJECTED
                ):
                    return record
            return None

    def pending_activation(self, task_id: str) -> ProposalRecord | None:
        with self._lock:
            pending = [
                record
                for record in self._records.values()
                if record.proposal.task_id == task_id and record.status is ProposalStatus.ACTIVATING
            ]
            if not pending:
                return None
            pending.sort(key=lambda item: item.updated_at, reverse=True)
            return pending[0]


class JsonPlanningRepository(InMemoryPlanningRepository):
    """Atomic durable proposal-state store used by the self-hosted reference deployment."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        super().__init__()
        self._load()

    def create(self, record: ProposalRecord) -> ProposalRecord:
        with self._lock:
            before = dict(self._records)
            stored = super().create(record)
            if self._records == before:
                return stored
            try:
                self._persist()
            except Exception:
                self._records = before
                raise
            return stored

    def save(self, record: ProposalRecord, *, expected_revision: int) -> ProposalRecord:
        with self._lock:
            before = dict(self._records)
            stored = super().save(record, expected_revision=expected_revision)
            try:
                self._persist()
            except Exception:
                self._records = before
                raise
            return stored

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = cast(object, json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid planning store: {exc}",
            ) from exc
        if not isinstance(raw, dict):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "planning store must be an object")
        if raw.get("schema_version") != PLANNING_STORE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "unsupported planning store schema version",
            )
        records = raw.get("records")
        if not isinstance(records, list):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "planning records must be a list")
        loaded: dict[str, ProposalRecord] = {}
        try:
            for value in records:
                record = _record_from_json(value)
                proposal_id = record.proposal.proposal_id
                if proposal_id in loaded:
                    raise ValueError(f"duplicate proposal_id {proposal_id}")
                loaded[proposal_id] = record
        except (TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"invalid persisted planning record: {exc}",
            ) from exc
        self._records = loaded
        self._validate_loaded_uniqueness()

    def _validate_loaded_uniqueness(self) -> None:
        idempotency: set[tuple[str, str]] = set()
        active_triggers: set[tuple[str, str]] = set()
        for record in self._records.values():
            idempotency_key = (record.proposal.task_id, record.idempotency_key)
            if idempotency_key in idempotency:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "planning store contains duplicate idempotency key",
                )
            idempotency.add(idempotency_key)
            if record.status is not ProposalStatus.REJECTED:
                trigger_key = (record.proposal.task_id, record.trigger_fingerprint)
                if trigger_key in active_triggers:
                    raise ContractError(
                        ErrorCode.INVALID_CONFIGURATION,
                        "planning store contains duplicate active trigger fingerprint",
                    )
                active_triggers.add(trigger_key)

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": PLANNING_STORE_SCHEMA_VERSION,
            "records": [_record_to_json(record) for record in self.list_all()],
        }
        encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, self.path)


def advance_record(
    record: ProposalRecord,
    *,
    status: ProposalStatus | None = None,
    activation_plan_id: str | None = None,
    approval_id: str | None = None,
    failure_reason: str | None = None,
) -> ProposalRecord:
    """Return the next immutable proposal-state revision."""

    return replace(
        record,
        status=status or record.status,
        activation_plan_id=(
            activation_plan_id if activation_plan_id is not None else record.activation_plan_id
        ),
        approval_id=approval_id if approval_id is not None else record.approval_id,
        failure_reason=failure_reason if failure_reason is not None else record.failure_reason,
        revision=record.revision + 1,
        updated_at=datetime.now(UTC),
    )


def _record_to_json(record: ProposalRecord) -> dict[str, Any]:
    return {
        "proposal": _proposal_to_json(record.proposal),
        "status": record.status.value,
        "idempotency_key": record.idempotency_key,
        "validation": {
            "valid": record.validation.valid,
            "errors": list(record.validation.errors),
            "warnings": list(record.validation.warnings),
            "approval_required": record.validation.approval_required,
        },
        "trigger_fingerprint": record.trigger_fingerprint,
        "revision": record.revision,
        "activation_plan_id": record.activation_plan_id,
        "approval_id": record.approval_id,
        "failure_reason": record.failure_reason,
        "updated_at": record.updated_at.isoformat(),
    }


def _record_from_json(value: object) -> ProposalRecord:
    raw = _mapping(value, "planning record")
    validation_raw = _mapping(raw.get("validation"), "proposal validation")
    return ProposalRecord(
        proposal=_proposal_from_json(raw.get("proposal")),
        status=ProposalStatus(_string(raw, "status")),
        idempotency_key=_string(raw, "idempotency_key"),
        validation=ProposalValidation(
            valid=_bool(validation_raw, "valid"),
            errors=_strings(validation_raw.get("errors", []), "validation errors"),
            warnings=_strings(validation_raw.get("warnings", []), "validation warnings"),
            approval_required=_bool(validation_raw, "approval_required"),
        ),
        trigger_fingerprint=_string(raw, "trigger_fingerprint"),
        revision=_int(raw, "revision"),
        activation_plan_id=_optional_string(raw, "activation_plan_id"),
        approval_id=_optional_string(raw, "approval_id"),
        failure_reason=_optional_string(raw, "failure_reason"),
        updated_at=_datetime(raw, "updated_at"),
    )


def _proposal_to_json(proposal: PlanProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "task_id": proposal.task_id,
        "task_revision": proposal.task_revision,
        "plan_revision": proposal.plan_revision,
        "trigger": proposal.trigger.value,
        "summary": proposal.summary,
        "steps": [_step_to_json(step) for step in proposal.steps],
        "planner": {
            "planner_id": proposal.planner.planner_id,
            "kind": proposal.planner.kind.value,
            "version": proposal.planner.version,
        },
        "base_plan_id": proposal.base_plan_id,
        "reason": proposal.reason,
        "assumptions": list(proposal.assumptions),
        "constraints": list(proposal.constraints),
        "evidence_refs": list(proposal.evidence_refs),
        "model_config_id": proposal.model_config_id,
        "supersedes_proposal_id": proposal.supersedes_proposal_id,
        "created_at": proposal.created_at.isoformat(),
    }


def _proposal_from_json(value: object) -> PlanProposal:
    raw = _mapping(value, "proposal")
    planner_raw = _mapping(raw.get("planner"), "planner descriptor")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list):
        raise ValueError("proposal steps must be a list")
    return PlanProposal(
        proposal_id=_string(raw, "proposal_id"),
        task_id=_string(raw, "task_id"),
        task_revision=_int(raw, "task_revision"),
        plan_revision=_int(raw, "plan_revision"),
        trigger=PlanningTrigger(_string(raw, "trigger")),
        summary=_string(raw, "summary"),
        steps=tuple(_step_from_json(item) for item in steps_raw),
        planner=PlannerDescriptor(
            planner_id=_string(planner_raw, "planner_id"),
            kind=PlannerKind(_string(planner_raw, "kind")),
            version=_string(planner_raw, "version"),
        ),
        base_plan_id=_optional_string(raw, "base_plan_id"),
        reason=_optional_string(raw, "reason"),
        assumptions=_strings(raw.get("assumptions", []), "assumptions"),
        constraints=_strings(raw.get("constraints", []), "constraints"),
        evidence_refs=_strings(raw.get("evidence_refs", []), "evidence_refs"),
        model_config_id=_optional_string(raw, "model_config_id"),
        supersedes_proposal_id=_optional_string(raw, "supersedes_proposal_id"),
        created_at=_datetime(raw, "created_at"),
    )


def _step_to_json(step: PlanningStepDraft) -> dict[str, Any]:
    assignment = None
    if step.assignment is not None:
        assignment = {
            "agent_id": step.assignment.agent_id,
            "agent_revision": step.assignment.agent_revision,
            "team_id": step.assignment.team_id,
            "team_revision": step.assignment.team_revision,
            "role_requirement": step.assignment.role_requirement,
            "rationale": step.assignment.rationale,
        }
    return {
        "key": step.key,
        "title": step.title,
        "objective": step.objective,
        "depends_on": list(step.depends_on),
        "assignment": assignment,
        "capability_requirements": [
            {
                "capability_id": requirement.capability_id,
                "exact_version": requirement.exact_version,
                "required_features": list(requirement.required_features),
                "required": requirement.required,
            }
            for requirement in step.capability_requirements
        ],
        "model_requirements": _requirements_to_json(step.model_requirements),
        "requires_model": step.requires_model,
        "workspace_id": step.workspace_id,
        "input_refs": list(step.input_refs),
        "output_refs": list(step.output_refs),
        "expected_evidence": list(step.expected_evidence),
        "verification_policy_refs": list(step.verification_policy_refs),
        "reuse_step_ids": list(step.reuse_step_ids),
        "metadata": dict(step.metadata),
    }


def _step_from_json(value: object) -> PlanningStepDraft:
    raw = _mapping(value, "planning step")
    assignment_raw = raw.get("assignment")
    assignment = None
    if assignment_raw is not None:
        assignment_map = _mapping(assignment_raw, "assignment")
        assignment = AgentAssignment(
            agent_id=_optional_string(assignment_map, "agent_id"),
            agent_revision=_optional_int(assignment_map, "agent_revision"),
            team_id=_optional_string(assignment_map, "team_id"),
            team_revision=_optional_int(assignment_map, "team_revision"),
            role_requirement=_optional_string(assignment_map, "role_requirement"),
            rationale=_optional_text(assignment_map, "rationale"),
        )
    requirements_raw = raw.get("capability_requirements", [])
    if not isinstance(requirements_raw, list):
        raise ValueError("capability_requirements must be a list")
    capability_requirements = tuple(
        CapabilityRequirement(
            capability_id=_string(item_map, "capability_id"),
            exact_version=_optional_string(item_map, "exact_version"),
            required_features=_strings(
                item_map.get("required_features", []),
                "required_features",
            ),
            required=_bool(item_map, "required"),
        )
        for item in requirements_raw
        for item_map in [_mapping(item, "capability requirement")]
    )
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("planning step metadata must be an object")
    return PlanningStepDraft(
        key=_string(raw, "key"),
        title=_string(raw, "title"),
        objective=_optional_text(raw, "objective"),
        depends_on=_strings(raw.get("depends_on", []), "depends_on"),
        assignment=assignment,
        capability_requirements=capability_requirements,
        model_requirements=_requirements_from_json(raw.get("model_requirements", {})),
        requires_model=_bool(raw, "requires_model"),
        workspace_id=_optional_string(raw, "workspace_id"),
        input_refs=_strings(raw.get("input_refs", []), "input_refs"),
        output_refs=_strings(raw.get("output_refs", []), "output_refs"),
        expected_evidence=_strings(raw.get("expected_evidence", []), "expected_evidence"),
        verification_policy_refs=_strings(
            raw.get("verification_policy_refs", []),
            "verification_policy_refs",
        ),
        reuse_step_ids=_strings(raw.get("reuse_step_ids", []), "reuse_step_ids"),
        metadata=cast(dict[str, Any], metadata),
    )


def _requirements_to_json(value: RoutingRequirements) -> dict[str, Any]:
    return {
        "explicit_model_id": value.explicit_model_id,
        "min_context_window": value.min_context_window,
        "tool_calling": value.tool_calling,
        "structured_output": value.structured_output,
        "streaming": value.streaming,
        "modalities": list(value.modalities),
        "reasoning": list(value.reasoning),
        "local_only": value.local_only,
        "self_hosted_only": value.self_hosted_only,
    }


def _requirements_from_json(value: object) -> RoutingRequirements:
    raw = _mapping(value, "model requirements")
    return RoutingRequirements(
        explicit_model_id=_optional_string(raw, "explicit_model_id"),
        min_context_window=_optional_int(raw, "min_context_window"),
        tool_calling=_bool(raw, "tool_calling"),
        structured_output=_bool(raw, "structured_output"),
        streaming=_bool(raw, "streaming"),
        modalities=_strings(raw.get("modalities", []), "modalities"),
        reasoning=_strings(raw.get("reasoning", []), "reasoning"),
        local_only=_bool(raw, "local_only"),
        self_hosted_only=_bool(raw, "self_hosted_only"),
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string when supplied")
    return value


def _optional_text(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer when supplied")
    return value


def _bool(raw: dict[str, Any], key: str) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _strings(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must contain non-blank strings")
    return tuple(cast(list[str], value))


def _datetime(raw: dict[str, Any], key: str) -> datetime:
    value = _string(raw, key)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed
