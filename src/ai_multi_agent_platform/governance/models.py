"""Canonical optional Proposal/Specification governance models for issue #501."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Literal

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id
from ai_multi_agent_platform.security import RiskClassification

GOVERNANCE_SCHEMA_VERSION = "1.0"


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    NEEDS_SPEC = "needs_spec"
    READY = "ready"
    DISMISSED = "dismissed"
    SUPERSEDED = "superseded"
    CONVERTED_TO_TASK = "converted_to_task"


class ConversionStatus(StrEnum):
    RESERVED = "reserved"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True, kw_only=True)
class Proposal:
    """Versioned intake artifact that never owns executable Task lifecycle state."""

    title: str
    summary: str
    reason: str
    owner_ref: OwnerRef
    requester_ref: str
    source: str
    id: str = field(default_factory=lambda: new_id("proposal"))
    status: ProposalStatus = ProposalStatus.DRAFT
    project_id: str | None = None
    workspace_id: str | None = None
    evidence_refs: tuple[str, ...] = ()
    confidence: float | None = None
    expected_value: float | None = None
    risk: RiskClassification = RiskClassification.STANDARD
    fingerprint: str | None = None
    supersedes_id: str | None = None
    superseded_by_id: str | None = None
    expires_at: datetime | None = None
    revision: int = 1
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    converted_task_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = GOVERNANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.id, "proposal")
        _require_text(self.title, "proposal title")
        _require_text(self.summary, "proposal summary")
        _require_text(self.reason, "proposal reason")
        _require_text(self.requester_ref, "proposal requester_ref")
        _require_text(self.source, "proposal source")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        if self.supersedes_id is not None:
            validate_id(self.supersedes_id, "proposal")
        if self.superseded_by_id is not None:
            validate_id(self.superseded_by_id, "proposal")
        if self.converted_task_id is not None:
            validate_id(self.converted_task_id, "task")
        if self.revision < 1:
            raise ValueError("proposal revision must be >= 1")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("proposal confidence must be between 0 and 1")
        _require_aware(self.created_at, "proposal created_at")
        _require_aware(self.updated_at, "proposal updated_at")
        if self.expires_at is not None:
            _require_aware(self.expires_at, "proposal expires_at")
        if any(not value.strip() for value in self.evidence_refs):
            raise ValueError("proposal evidence_refs must not contain blank values")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True, slots=True, kw_only=True)
class SpecificationRevision:
    """Immutable revision of the reviewable pre-execution contract."""

    problem: str
    goal: str
    scope: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    owner_ref: OwnerRef
    requester_ref: str
    id: str = field(default_factory=lambda: new_id("specification"))
    revision: int = 1
    proposal_id: str | None = None
    goal_id: str | None = None
    task_intake_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    out_of_scope: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk: RiskClassification = RiskClassification.STANDARD
    required_capabilities: tuple[str, ...] = ()
    model_requirements: Mapping[str, JsonValue] = field(default_factory=dict)
    agent_requirements: Mapping[str, JsonValue] = field(default_factory=dict)
    data_security_constraints: tuple[str, ...] = ()
    validation_strategy: tuple[str, ...] = ()
    required_tests: tuple[str, ...] = ()
    verification_requirements: tuple[str, ...] = ()
    required_human_gates: tuple[str, ...] = ()
    decomposition_hints: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    provenance: Mapping[str, JsonValue] = field(default_factory=dict)
    content_digest: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = GOVERNANCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.id, "specification")
        _require_text(self.problem, "specification problem")
        _require_text(self.goal, "specification goal")
        _require_text(self.requester_ref, "specification requester_ref")
        if self.revision < 1:
            raise ValueError("specification revision must be >= 1")
        intake_refs = tuple(
            value
            for value in (self.proposal_id, self.goal_id, self.task_intake_id)
            if value is not None
        )
        if len(intake_refs) != 1:
            raise ValueError(
                "specification requires exactly one proposal/goal/task intake reference"
            )
        if self.proposal_id is not None:
            validate_id(self.proposal_id, "proposal")
        if self.goal_id is not None:
            validate_id(self.goal_id, "goal")
        if self.task_intake_id is not None:
            validate_id(self.task_intake_id, "task")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.workspace_id is not None:
            validate_id(self.workspace_id, "workspace")
        if not self.scope:
            raise ValueError("specification scope must not be empty")
        if not self.acceptance_criteria:
            raise ValueError("specification acceptance_criteria must not be empty")
        for name in _SPEC_TUPLE_FIELDS:
            values = tuple(getattr(self, name))
            if any(not value.strip() for value in values):
                raise ValueError(f"specification {name} must not contain blank values")
            object.__setattr__(self, name, values)
        object.__setattr__(
            self, "model_requirements", MappingProxyType(dict(self.model_requirements))
        )
        object.__setattr__(
            self, "agent_requirements", MappingProxyType(dict(self.agent_requirements))
        )
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        _require_aware(self.created_at, "specification created_at")
        computed = specification_content_digest(self)
        if self.content_digest and self.content_digest != computed:
            raise ValueError("specification content_digest does not match canonical content")
        object.__setattr__(self, "content_digest", computed)

    @property
    def approval_required(self) -> bool:
        return bool(self.required_human_gates) or self.risk in {
            RiskClassification.HIGH,
            RiskClassification.CRITICAL,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskConversion:
    """Durable idempotency/provenance link from one exact Specification to one Task."""

    specification_id: str
    specification_revision: int
    specification_digest: str
    task_id: str
    proposal_id: str | None = None
    approval_id: str | None = None
    status: ConversionStatus = ConversionStatus.RESERVED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.specification_id, "specification")
        validate_id(self.task_id, "task")
        if self.proposal_id is not None:
            validate_id(self.proposal_id, "proposal")
        if self.approval_id is not None:
            validate_id(self.approval_id, "approval")
        if self.specification_revision < 1:
            raise ValueError("conversion specification_revision must be >= 1")
        if len(self.specification_digest) != 64:
            raise ValueError("conversion specification_digest must be a SHA-256 digest")
        _require_aware(self.created_at, "conversion created_at")
        if self.completed_at is not None:
            _require_aware(self.completed_at, "conversion completed_at")


@dataclass(frozen=True, slots=True, kw_only=True)
class GovernanceAuditEvent:
    """Content-minimal governance audit event; unrestricted Specification text is excluded."""

    event_type: str
    resource_type: Literal["proposal", "specification", "conversion"]
    resource_id: str
    actor_ref: str
    id: str = field(default_factory=lambda: new_id("event"))
    project_id: str | None = None
    revision: int | None = None
    digest: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        validate_id(self.id, "event")
        _require_text(self.event_type, "governance event_type")
        _require_text(self.resource_id, "governance resource_id")
        _require_text(self.actor_ref, "governance actor_ref")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.revision is not None and self.revision < 1:
            raise ValueError("governance audit revision must be >= 1")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        _require_aware(self.occurred_at, "governance occurred_at")


_SPEC_TUPLE_FIELDS = (
    "scope",
    "acceptance_criteria",
    "out_of_scope",
    "dependencies",
    "constraints",
    "required_capabilities",
    "data_security_constraints",
    "validation_strategy",
    "required_tests",
    "verification_requirements",
    "required_human_gates",
    "decomposition_hints",
    "assumptions",
    "open_questions",
)


def specification_content_digest(specification: SpecificationRevision) -> str:
    """Return a stable digest of all materially reviewable Specification content."""

    payload: dict[str, Any] = {
        "schema_version": specification.schema_version,
        "proposal_id": specification.proposal_id,
        "goal_id": specification.goal_id,
        "task_intake_id": specification.task_intake_id,
        "project_id": specification.project_id,
        "workspace_id": specification.workspace_id,
        "problem": specification.problem,
        "goal": specification.goal,
        "scope": list(specification.scope),
        "out_of_scope": list(specification.out_of_scope),
        "acceptance_criteria": list(specification.acceptance_criteria),
        "dependencies": list(specification.dependencies),
        "constraints": list(specification.constraints),
        "risk": specification.risk.value,
        "required_capabilities": list(specification.required_capabilities),
        "model_requirements": _json_ready(specification.model_requirements),
        "agent_requirements": _json_ready(specification.agent_requirements),
        "data_security_constraints": list(specification.data_security_constraints),
        "validation_strategy": list(specification.validation_strategy),
        "required_tests": list(specification.required_tests),
        "verification_requirements": list(specification.verification_requirements),
        "required_human_gates": list(specification.required_human_gates),
        "decomposition_hints": list(specification.decomposition_hints),
        "assumptions": list(specification.assumptions),
        "open_questions": list(specification.open_questions),
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_ready(item) for item in value]
    return value


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
