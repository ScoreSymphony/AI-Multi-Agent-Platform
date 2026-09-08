"""Canonical governed feedback and learning-candidate models for issue #595."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance, new_id, validate_id
from ai_multi_agent_platform.security.authorization import RiskClassification

LEARNING_SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(sorted((_freeze_json(item) for item in value), key=repr))
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class FeedbackType(StrEnum):
    CORRECTION = "correction"
    OUTCOME_ACCEPTED = "outcome_accepted"
    OUTCOME_REJECTED = "outcome_rejected"
    PREFERENCE = "preference"
    RATING = "rating"
    FINDING = "finding"
    COMMENT = "comment"


class LearningSourceType(StrEnum):
    USER_FEEDBACK = "user_feedback"
    VERIFICATION = "verification"
    EVALUATION = "evaluation"
    RUN_FAILURE_PATTERN = "run_failure_pattern"
    PLANNING_FAILURE_PATTERN = "planning_failure_pattern"
    RESEARCH_EVIDENCE = "research_evidence"
    OPERATOR_PROPOSAL = "operator_proposal"


class LearningCandidateStatus(StrEnum):
    PROPOSED = "proposed"
    EVALUATING = "evaluating"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    PROMOTED = "promoted"


class LearningTargetType(StrEnum):
    AGENT = "agent"
    SKILL = "skill"
    MODEL_ROUTING_PROFILE = "model_routing_profile"
    TEMPLATE = "template"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    PLANNER_POLICY = "planner_policy"
    VERIFICATION_POLICY = "verification_policy"
    EVALUATION_POLICY = "evaluation_policy"
    DOCUMENTATION = "documentation"


@dataclass(frozen=True, slots=True)
class LearningReference:
    """Exact evidence/reference binding. Revision/digest are preserved when known."""

    kind: str
    resource_id: str
    revision: str | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.kind, "learning reference kind")
        _require_nonblank(self.resource_id, "learning reference resource_id")
        if self.revision is not None:
            _require_nonblank(self.revision, "learning reference revision")
        if self.digest is not None:
            _require_nonblank(self.digest, "learning reference digest")

    @property
    def key(self) -> tuple[str, str, str | None, str | None]:
        return (self.kind, self.resource_id, self.revision, self.digest)


@dataclass(frozen=True, slots=True)
class LearningTarget:
    resource_type: LearningTargetType
    resource_id: str
    revision: int

    def __post_init__(self) -> None:
        _require_nonblank(self.resource_id, "learning target resource_id")
        if self.revision < 1:
            raise ValueError("learning target revision must be >= 1")


@dataclass(frozen=True, slots=True)
class LearningGatePlan:
    """Versioned fail-closed policy snapshot used to accept/promote one candidate."""

    policy_id: str
    policy_version: int
    require_evaluation: bool = True
    require_verification: bool = False
    require_regression_free: bool = True
    approval_required_risks: tuple[RiskClassification, ...] = (
        RiskClassification.HIGH,
        RiskClassification.CRITICAL,
    )
    automatic_promotion_allowed: bool = False
    evaluation_suite_refs: tuple[str, ...] = ()
    verification_policy_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank(self.policy_id, "learning gate policy_id")
        if self.policy_version < 1:
            raise ValueError("learning gate policy_version must be >= 1")
        if not self.require_evaluation and not self.require_verification:
            raise ValueError(
                "promotable learning policy must require evaluation and/or verification"
            )
        if len(set(self.approval_required_risks)) != len(self.approval_required_risks):
            raise ValueError("approval-required risk classes must be unique")
        for value in (*self.evaluation_suite_refs, *self.verification_policy_refs):
            _require_nonblank(value, "learning gate reference")


@dataclass(frozen=True, slots=True)
class FeedbackRecord:
    feedback_type: FeedbackType
    subject: LearningReference
    creator_ref: str
    comment: str | None = None
    target: LearningTarget | None = None
    project_id: str | None = None
    feedback_id: str = field(default_factory=lambda: new_id("feedback"))
    created_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None
    schema_version: str = LEARNING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.feedback_id, "feedback")
        _require_nonblank(self.creator_ref, "feedback creator_ref")
        if self.comment is not None:
            _require_nonblank(self.comment, "feedback comment")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        _require_aware(self.created_at, "feedback created_at")

    @property
    def content_digest(self) -> str:
        return _digest(
            {
                "feedback_type": self.feedback_type.value,
                "subject": reference_to_dict(self.subject),
                "creator_ref": self.creator_ref,
                "comment": self.comment,
                "target": None if self.target is None else target_to_dict(self.target),
                "project_id": self.project_id,
                "feedback_id": self.feedback_id,
                "created_at": self.created_at,
                "schema_version": self.schema_version,
            }
        )


@dataclass(frozen=True, slots=True)
class PromotionReceipt:
    target_type: LearningTargetType
    target_id: str
    previous_revision: int
    new_revision: int
    canonical_ref: str
    candidate_digest: str
    already_applied: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.target_id, "promotion target_id")
        if self.previous_revision < 1 or self.new_revision <= self.previous_revision:
            raise ValueError("promotion receipt revisions are invalid")
        _require_nonblank(self.canonical_ref, "promotion canonical_ref")
        _require_nonblank(self.candidate_digest, "promotion candidate_digest")


@dataclass(frozen=True, slots=True)
class LearningCandidate:
    source_type: LearningSourceType
    problem: str
    target: LearningTarget
    improvement_type: str
    expected_benefit: str
    risk: RiskClassification
    gate_plan: LearningGatePlan
    creator_ref: str
    source_refs: tuple[LearningReference, ...]
    evidence_refs: tuple[LearningReference, ...] = ()
    proposed_change: Mapping[str, JsonValue] = field(default_factory=dict)
    proposed_artifact_ref: LearningReference | None = None
    evaluation_run_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    status: LearningCandidateStatus = LearningCandidateStatus.PROPOSED
    learning_candidate_id: str = field(default_factory=lambda: new_id("learning_candidate"))
    revision: int = 1
    project_id: str | None = None
    superseded_by: str | None = None
    promotion: PromotionReceipt | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None
    schema_version: str = LEARNING_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.learning_candidate_id, "learning_candidate")
        if self.revision < 1:
            raise ValueError("learning candidate revision must be >= 1")
        _require_nonblank(self.problem, "learning candidate problem")
        _require_nonblank(self.improvement_type, "learning candidate improvement_type")
        _require_nonblank(self.expected_benefit, "learning candidate expected_benefit")
        _require_nonblank(self.creator_ref, "learning candidate creator_ref")
        if not self.source_refs:
            raise ValueError("learning candidate requires at least one source reference")
        if len({item.key for item in self.source_refs}) != len(self.source_refs):
            raise ValueError("learning candidate source references must be unique")
        if len({item.key for item in self.evidence_refs}) != len(self.evidence_refs):
            raise ValueError("learning candidate evidence references must be unique")
        if len(set(self.evaluation_run_ids)) != len(self.evaluation_run_ids):
            raise ValueError("learning candidate evaluation run IDs must be unique")
        if len(set(self.verification_ids)) != len(self.verification_ids):
            raise ValueError("learning candidate verification IDs must be unique")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.superseded_by is not None:
            validate_id(self.superseded_by, "learning_candidate")
            if self.status is not LearningCandidateStatus.SUPERSEDED:
                raise ValueError("superseded_by requires superseded status")
        if self.status is LearningCandidateStatus.PROMOTED and self.promotion is None:
            raise ValueError("promoted learning candidate requires a promotion receipt")
        if self.status is not LearningCandidateStatus.PROMOTED and self.promotion is not None:
            raise ValueError("promotion receipt is only valid for promoted candidates")
        _require_aware(self.created_at, "learning candidate created_at")
        _require_aware(self.updated_at, "learning candidate updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("learning candidate updated_at cannot precede created_at")
        object.__setattr__(
            self,
            "proposed_change",
            cast(Mapping[str, JsonValue], _freeze_json(self.proposed_change)),
        )

    @property
    def id(self) -> str:
        return self.learning_candidate_id

    @property
    def content_digest(self) -> str:
        return _digest(candidate_to_dict(self, include_digest=False))

    @property
    def dedupe_key(self) -> str:
        """Stable problem/target/proposal identity; source refs are linked separately."""
        return _digest(
            {
                "source_type": self.source_type.value,
                "problem": self.problem,
                "target": target_to_dict(self.target),
                "improvement_type": self.improvement_type,
                "proposed_change": self.proposed_change,
                "proposed_artifact_ref": (
                    None
                    if self.proposed_artifact_ref is None
                    else reference_to_dict(self.proposed_artifact_ref)
                ),
            }
        )


def reference_to_dict(reference: LearningReference) -> dict[str, JsonValue]:
    return {
        "kind": reference.kind,
        "resource_id": reference.resource_id,
        "revision": reference.revision,
        "digest": reference.digest,
    }


def reference_from_dict(payload: Mapping[str, Any]) -> LearningReference:
    return LearningReference(
        kind=str(payload["kind"]),
        resource_id=str(payload["resource_id"]),
        revision=None if payload.get("revision") is None else str(payload["revision"]),
        digest=None if payload.get("digest") is None else str(payload["digest"]),
    )


def target_to_dict(target: LearningTarget) -> dict[str, JsonValue]:
    return {
        "resource_type": target.resource_type.value,
        "resource_id": target.resource_id,
        "revision": target.revision,
    }


def target_from_dict(payload: Mapping[str, Any]) -> LearningTarget:
    return LearningTarget(
        resource_type=LearningTargetType(str(payload["resource_type"])),
        resource_id=str(payload["resource_id"]),
        revision=int(payload["revision"]),
    )


def gate_plan_to_dict(plan: LearningGatePlan) -> dict[str, JsonValue]:
    return {
        "policy_id": plan.policy_id,
        "policy_version": plan.policy_version,
        "require_evaluation": plan.require_evaluation,
        "require_verification": plan.require_verification,
        "require_regression_free": plan.require_regression_free,
        "approval_required_risks": [item.value for item in plan.approval_required_risks],
        "automatic_promotion_allowed": plan.automatic_promotion_allowed,
        "evaluation_suite_refs": list(plan.evaluation_suite_refs),
        "verification_policy_refs": list(plan.verification_policy_refs),
    }


def gate_plan_from_dict(payload: Mapping[str, Any]) -> LearningGatePlan:
    return LearningGatePlan(
        policy_id=str(payload["policy_id"]),
        policy_version=int(payload["policy_version"]),
        require_evaluation=bool(payload.get("require_evaluation", True)),
        require_verification=bool(payload.get("require_verification", False)),
        require_regression_free=bool(payload.get("require_regression_free", True)),
        approval_required_risks=tuple(
            RiskClassification(str(item)) for item in payload.get("approval_required_risks", ())
        ),
        automatic_promotion_allowed=bool(payload.get("automatic_promotion_allowed", False)),
        evaluation_suite_refs=tuple(str(item) for item in payload.get("evaluation_suite_refs", ())),
        verification_policy_refs=tuple(
            str(item) for item in payload.get("verification_policy_refs", ())
        ),
    )


def promotion_to_dict(receipt: PromotionReceipt) -> dict[str, JsonValue]:
    return {
        "target_type": receipt.target_type.value,
        "target_id": receipt.target_id,
        "previous_revision": receipt.previous_revision,
        "new_revision": receipt.new_revision,
        "canonical_ref": receipt.canonical_ref,
        "candidate_digest": receipt.candidate_digest,
        "already_applied": receipt.already_applied,
    }


def promotion_from_dict(payload: Mapping[str, Any]) -> PromotionReceipt:
    return PromotionReceipt(
        target_type=LearningTargetType(str(payload["target_type"])),
        target_id=str(payload["target_id"]),
        previous_revision=int(payload["previous_revision"]),
        new_revision=int(payload["new_revision"]),
        canonical_ref=str(payload["canonical_ref"]),
        candidate_digest=str(payload["candidate_digest"]),
        already_applied=bool(payload.get("already_applied", False)),
    )


def candidate_to_dict(
    candidate: LearningCandidate, *, include_digest: bool = True
) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "learning_candidate_id": candidate.learning_candidate_id,
        "revision": candidate.revision,
        "source_type": candidate.source_type.value,
        "problem": candidate.problem,
        "target": target_to_dict(candidate.target),
        "improvement_type": candidate.improvement_type,
        "expected_benefit": candidate.expected_benefit,
        "risk": candidate.risk.value,
        "gate_plan": gate_plan_to_dict(candidate.gate_plan),
        "creator_ref": candidate.creator_ref,
        "source_refs": [reference_to_dict(item) for item in candidate.source_refs],
        "evidence_refs": [reference_to_dict(item) for item in candidate.evidence_refs],
        "proposed_change": _jsonable(candidate.proposed_change),
        "proposed_artifact_ref": (
            None
            if candidate.proposed_artifact_ref is None
            else reference_to_dict(candidate.proposed_artifact_ref)
        ),
        "evaluation_run_ids": list(candidate.evaluation_run_ids),
        "verification_ids": list(candidate.verification_ids),
        "status": candidate.status.value,
        "project_id": candidate.project_id,
        "superseded_by": candidate.superseded_by,
        "promotion": None
        if candidate.promotion is None
        else promotion_to_dict(candidate.promotion),
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
        "provenance": (
            None
            if candidate.provenance is None
            else {
                "source": candidate.provenance.source,
                "actor_ref": candidate.provenance.actor_ref,
                "details": _jsonable(candidate.provenance.details),
            }
        ),
        "schema_version": candidate.schema_version,
    }
    if include_digest:
        payload["content_digest"] = candidate.content_digest
    return payload


def candidate_from_dict(payload: Mapping[str, Any]) -> LearningCandidate:
    provenance_payload = payload.get("provenance")
    artifact_payload = payload.get("proposed_artifact_ref")
    promotion_payload = payload.get("promotion")
    return LearningCandidate(
        learning_candidate_id=str(payload["learning_candidate_id"]),
        revision=int(payload["revision"]),
        source_type=LearningSourceType(str(payload["source_type"])),
        problem=str(payload["problem"]),
        target=target_from_dict(cast(Mapping[str, Any], payload["target"])),
        improvement_type=str(payload["improvement_type"]),
        expected_benefit=str(payload["expected_benefit"]),
        risk=RiskClassification(str(payload["risk"])),
        gate_plan=gate_plan_from_dict(cast(Mapping[str, Any], payload["gate_plan"])),
        creator_ref=str(payload["creator_ref"]),
        source_refs=tuple(
            reference_from_dict(cast(Mapping[str, Any], item))
            for item in cast(list[Any], payload["source_refs"])
        ),
        evidence_refs=tuple(
            reference_from_dict(cast(Mapping[str, Any], item))
            for item in cast(list[Any], payload.get("evidence_refs", []))
        ),
        proposed_change=cast(Mapping[str, JsonValue], payload.get("proposed_change", {})),
        proposed_artifact_ref=(
            None
            if artifact_payload is None
            else reference_from_dict(cast(Mapping[str, Any], artifact_payload))
        ),
        evaluation_run_ids=tuple(str(item) for item in payload.get("evaluation_run_ids", ())),
        verification_ids=tuple(str(item) for item in payload.get("verification_ids", ())),
        status=LearningCandidateStatus(str(payload["status"])),
        project_id=None if payload.get("project_id") is None else str(payload["project_id"]),
        superseded_by=(
            None if payload.get("superseded_by") is None else str(payload["superseded_by"])
        ),
        promotion=(
            None
            if promotion_payload is None
            else promotion_from_dict(cast(Mapping[str, Any], promotion_payload))
        ),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        provenance=(
            None
            if provenance_payload is None
            else Provenance(
                source=str(cast(Mapping[str, Any], provenance_payload)["source"]),
                actor_ref=(
                    None
                    if cast(Mapping[str, Any], provenance_payload).get("actor_ref") is None
                    else str(cast(Mapping[str, Any], provenance_payload)["actor_ref"])
                ),
                details=cast(
                    Mapping[str, Any],
                    cast(Mapping[str, Any], provenance_payload).get("details", {}),
                ),
            )
        ),
        schema_version=str(payload.get("schema_version", LEARNING_SCHEMA_VERSION)),
    )


def feedback_to_dict(record: FeedbackRecord) -> dict[str, JsonValue]:
    return {
        "feedback_id": record.feedback_id,
        "feedback_type": record.feedback_type.value,
        "subject": reference_to_dict(record.subject),
        "creator_ref": record.creator_ref,
        "comment": record.comment,
        "target": None if record.target is None else target_to_dict(record.target),
        "project_id": record.project_id,
        "created_at": record.created_at.isoformat(),
        "provenance": (
            None
            if record.provenance is None
            else {
                "source": record.provenance.source,
                "actor_ref": record.provenance.actor_ref,
                "details": _jsonable(record.provenance.details),
            }
        ),
        "schema_version": record.schema_version,
        "content_digest": record.content_digest,
    }


def feedback_from_dict(payload: Mapping[str, Any]) -> FeedbackRecord:
    provenance_payload = payload.get("provenance")
    target_payload = payload.get("target")
    return FeedbackRecord(
        feedback_id=str(payload["feedback_id"]),
        feedback_type=FeedbackType(str(payload["feedback_type"])),
        subject=reference_from_dict(cast(Mapping[str, Any], payload["subject"])),
        creator_ref=str(payload["creator_ref"]),
        comment=None if payload.get("comment") is None else str(payload["comment"]),
        target=(
            None
            if target_payload is None
            else target_from_dict(cast(Mapping[str, Any], target_payload))
        ),
        project_id=None if payload.get("project_id") is None else str(payload["project_id"]),
        created_at=datetime.fromisoformat(str(payload["created_at"])),
        provenance=(
            None
            if provenance_payload is None
            else Provenance(
                source=str(cast(Mapping[str, Any], provenance_payload)["source"]),
                actor_ref=(
                    None
                    if cast(Mapping[str, Any], provenance_payload).get("actor_ref") is None
                    else str(cast(Mapping[str, Any], provenance_payload)["actor_ref"])
                ),
                details=cast(
                    Mapping[str, Any],
                    cast(Mapping[str, Any], provenance_payload).get("details", {}),
                ),
            )
        ),
        schema_version=str(payload.get("schema_version", LEARNING_SCHEMA_VERSION)),
    )


def new_learning_candidate_id() -> str:
    return new_id("learning_candidate")


def new_feedback_id() -> str:
    return new_id("feedback")
