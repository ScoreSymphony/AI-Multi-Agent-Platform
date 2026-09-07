"""Canonical evidence-backed Decision Record models for issue #598."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id, validate_id

DECISION_SCHEMA_VERSION = "1.0"


class DecisionOutcome(StrEnum):
    ADOPT = "adopt"
    REJECT = "reject"
    DEFER = "defer"
    EXPERIMENTAL = "experimental"
    SUPERSEDE = "supersede"
    CUSTOM = "custom"


class DecisionStatus(StrEnum):
    CURRENT = "current"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class DecisionAlternativeStatus(StrEnum):
    CONSIDERED = "considered"
    REJECTED = "rejected"
    SELECTED = "selected"


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionReference:
    """Exact, backend-neutral reference to evidence or another canonical resource."""

    kind: str
    resource_id: str
    revision: str | int | None = None
    digest: str | None = None
    locator: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text(self.kind, "decision reference kind")
        _require_text(self.resource_id, "decision reference resource_id")
        if isinstance(self.revision, str) and not self.revision.strip():
            raise ValueError("decision reference revision must not be blank")
        if isinstance(self.revision, int) and self.revision < 1:
            raise ValueError("numeric decision reference revision must be >= 1")
        if self.digest is not None:
            _require_text(self.digest, "decision reference digest")
        if self.locator is not None:
            _require_text(self.locator, "decision reference locator")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionAlternative:
    """One explicitly considered option and its material evidence/trade-offs."""

    label: str
    status: DecisionAlternativeStatus = DecisionAlternativeStatus.CONSIDERED
    resource_ref: DecisionReference | None = None
    evidence_refs: tuple[DecisionReference, ...] = ()
    trade_offs: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.label, "decision alternative label")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "trade_offs", _clean_text_tuple(self.trade_offs, "trade_offs"))
        object.__setattr__(self, "unknowns", _clean_text_tuple(self.unknowns, "unknowns"))


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionRecord:
    """Immutable historical decision content.

    ``status`` is the status recorded in the immutable payload. Runtime supersession and
    withdrawal are append-only lifecycle relations represented by ``DecisionRecordView``;
    the original payload and digest are never rewritten.
    """

    title: str
    subject: str
    category: str
    scope_type: str
    question: str
    alternatives: tuple[DecisionAlternative, ...]
    outcome: DecisionOutcome
    rationale: str
    actor_ref: str
    id: str = field(default_factory=lambda: new_id("decision_record"))
    scope_id: str | None = None
    subject_ref: DecisionReference | None = None
    evidence_refs: tuple[DecisionReference, ...] = ()
    evaluation_refs: tuple[DecisionReference, ...] = ()
    finding_refs: tuple[DecisionReference, ...] = ()
    cost_resource_refs: tuple[DecisionReference, ...] = ()
    reviewer_refs: tuple[str, ...] = ()
    approval_ref: DecisionReference | None = None
    adr_ref: DecisionReference | None = None
    effective_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    review_at: datetime | None = None
    review_condition: str | None = None
    status: DecisionStatus = DecisionStatus.CURRENT
    supersedes: str | None = None
    revision: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    content_digest: str = ""
    schema_version: str = DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.id, "decision_record")
        _require_text(self.title, "decision title")
        _require_text(self.subject, "decision subject")
        _require_text(self.category, "decision category")
        _require_text(self.scope_type, "decision scope_type")
        _require_text(self.question, "decision question")
        _require_text(self.rationale, "decision rationale")
        _require_text(self.actor_ref, "decision actor_ref")
        if self.scope_id is not None:
            _require_text(self.scope_id, "decision scope_id")
        if self.supersedes is not None:
            validate_id(self.supersedes, "decision_record")
        if self.revision < 1:
            raise ValueError("decision revision must be >= 1")
        if not self.alternatives:
            raise ValueError("decision requires at least one considered alternative")
        object.__setattr__(self, "alternatives", tuple(self.alternatives))
        selected = sum(
            alternative.status is DecisionAlternativeStatus.SELECTED
            for alternative in self.alternatives
        )
        if selected > 1:
            raise ValueError("decision may have at most one selected alternative")
        if self.outcome in {
            DecisionOutcome.ADOPT,
            DecisionOutcome.EXPERIMENTAL,
            DecisionOutcome.CUSTOM,
        } and selected != 1:
            raise ValueError(f"{self.outcome.value} decision requires exactly one selected alternative")
        for name in (
            "evidence_refs",
            "evaluation_refs",
            "finding_refs",
            "cost_resource_refs",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(
            self,
            "reviewer_refs",
            _clean_text_tuple(self.reviewer_refs, "reviewer_refs"),
        )
        _require_aware(self.effective_at, "decision effective_at")
        _require_aware(self.created_at, "decision created_at")
        if self.review_at is not None:
            _require_aware(self.review_at, "decision review_at")
        if self.review_condition is not None:
            _require_text(self.review_condition, "decision review_condition")
        computed = decision_content_digest(self)
        if self.content_digest and self.content_digest != computed:
            raise ValueError("decision content_digest does not match canonical content")
        object.__setattr__(self, "content_digest", computed)


@dataclass(frozen=True, slots=True, kw_only=True)
class DecisionRecordView:
    """Current projection over one immutable DecisionRecord and append-only relations."""

    record: DecisionRecord
    status: DecisionStatus
    superseded_by: str | None = None
    withdrawn_at: datetime | None = None
    withdrawal_reason: str | None = None
    downstream_refs: tuple[DecisionReference, ...] = ()

    def __post_init__(self) -> None:
        if self.superseded_by is not None:
            validate_id(self.superseded_by, "decision_record")
        if self.withdrawn_at is not None:
            _require_aware(self.withdrawn_at, "decision withdrawn_at")
        if self.withdrawal_reason is not None:
            _require_text(self.withdrawal_reason, "decision withdrawal_reason")
        object.__setattr__(self, "downstream_refs", tuple(self.downstream_refs))


def decision_content_digest(record: DecisionRecord) -> str:
    """Return a stable SHA-256 digest of the immutable decision content."""

    payload: dict[str, JsonValue] = {
        "schema_version": record.schema_version,
        "title": record.title,
        "subject": record.subject,
        "category": record.category,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "question": record.question,
        "alternatives": [_alternative_json(item) for item in record.alternatives],
        "outcome": record.outcome.value,
        "rationale": record.rationale,
        "actor_ref": record.actor_ref,
        "subject_ref": _reference_json(record.subject_ref),
        "evidence_refs": [_reference_json(item) for item in record.evidence_refs],
        "evaluation_refs": [_reference_json(item) for item in record.evaluation_refs],
        "finding_refs": [_reference_json(item) for item in record.finding_refs],
        "cost_resource_refs": [_reference_json(item) for item in record.cost_resource_refs],
        "reviewer_refs": list(record.reviewer_refs),
        "approval_ref": _reference_json(record.approval_ref),
        "adr_ref": _reference_json(record.adr_ref),
        "effective_at": record.effective_at.isoformat(),
        "review_at": record.review_at.isoformat() if record.review_at is not None else None,
        "review_condition": record.review_condition,
        "status": record.status.value,
        "supersedes": record.supersedes,
        "revision": record.revision,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def reference_to_json(reference: DecisionReference) -> dict[str, JsonValue]:
    value = _reference_json(reference)
    assert isinstance(value, dict)
    return value


def alternative_to_json(alternative: DecisionAlternative) -> dict[str, JsonValue]:
    return _alternative_json(alternative)


def _reference_json(reference: DecisionReference | None) -> dict[str, JsonValue] | None:
    if reference is None:
        return None
    return {
        "kind": reference.kind,
        "resource_id": reference.resource_id,
        "revision": reference.revision,
        "digest": reference.digest,
        "locator": reference.locator,
        "metadata": dict(reference.metadata),
    }


def _alternative_json(alternative: DecisionAlternative) -> dict[str, JsonValue]:
    return {
        "label": alternative.label,
        "status": alternative.status.value,
        "resource_ref": _reference_json(alternative.resource_ref),
        "evidence_refs": [_reference_json(item) for item in alternative.evidence_refs],
        "trade_offs": list(alternative.trade_offs),
        "unknowns": list(alternative.unknowns),
    }


def _require_text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _clean_text_tuple(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if any(not value.strip() for value in normalized):
        raise ValueError(f"decision {name} must not contain blank values")
    return normalized
