"""Canonical research-evidence value objects for issue #589.

Research is evidence metadata layered on the canonical Task/Run runtime.  These models
must not become a second execution, storage, verification or authorization authority.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id, validate_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def _nonblank(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


def _metadata(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _digest(payload: Mapping[str, JsonValue]) -> str:
    encoded = json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


class ResearchClass(StrEnum):
    TASK_RESEARCH = "task_research"
    PROJECT_RESEARCH = "project_research"
    DOMAIN_RESEARCH = "domain_research"


class ResearchStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    REVIEW_REQUIRED = "review_required"
    DECISION_READY = "decision_ready"
    STALE = "stale"
    SUPERSEDED = "superseded"


class ResearchSourceType(StrEnum):
    WEB = "web"
    REPOSITORY = "repository"
    REPOSITORY_INTELLIGENCE = "repository_intelligence"
    DOCUMENT = "document"
    DATASET = "dataset"
    PAPER = "paper"
    API_RESPONSE = "api_response"
    OTHER = "other"


class SourceObservationState(StrEnum):
    CURRENT = "current"
    CHANGED = "changed"
    UNAVAILABLE = "unavailable"
    UNVERIFIABLE = "unverifiable"


class EvidenceFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNVERIFIABLE = "unverifiable"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ClaimConfidence(StrEnum):
    """Qualitative confidence; this is not a calibrated probability."""

    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXTUALIZES = "contextualizes"
    DERIVES_FROM = "derives_from"


class ResearchVerificationSubjectType(StrEnum):
    ITEM = "research_item"
    CLAIM = "research_claim"
    EVIDENCE = "research_evidence"


@dataclass(frozen=True, slots=True)
class FreshnessPolicy:
    max_age_seconds: float | None = None
    revalidate_on_source_change: bool = True

    def __post_init__(self) -> None:
        if self.max_age_seconds is not None and self.max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be greater than zero")


@dataclass(frozen=True, slots=True)
class ResearchItem:
    title: str
    question: str
    research_class: ResearchClass
    owner_ref: OwnerRef
    research_item_id: str = field(default_factory=lambda: new_id("research_item"))
    status: ResearchStatus = ResearchStatus.OPEN
    revision: int = 1
    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    run_id: str | None = None
    data_class: str = "internal"
    constraints: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    verification_ids: tuple[str, ...] = ()
    freshness_policy: FreshnessPolicy = FreshnessPolicy()
    supersedes_research_item_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.research_item_id, "research_item")
        _nonblank(self.title, "research title")
        _nonblank(self.question, "research question")
        _nonblank(self.data_class, "research data_class")
        if self.revision < 1:
            raise ValueError("research item revision must be >= 1")
        for value, prefix in (
            (self.project_id, "project"),
            (self.workspace_id, "workspace"),
            (self.task_id, "task"),
            (self.plan_id, "plan"),
            (self.run_id, "run"),
            (self.supersedes_research_item_id, "research_item"),
        ):
            _optional_id(value, prefix)
        for value in self.source_ids:
            validate_id(value, "research_source")
        for value in self.claim_ids:
            validate_id(value, "research_claim")
        for value in self.evidence_ids:
            validate_id(value, "research_evidence")
        for value in self.verification_ids:
            validate_id(value, "verification")
        for values, name in (
            (self.source_ids, "source_ids"),
            (self.claim_ids, "claim_ids"),
            (self.evidence_ids, "evidence_ids"),
            (self.verification_ids, "verification_ids"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"research {name} must be unique")
        _aware(self.created_at, "research item created_at")
        _aware(self.updated_at, "research item updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("research item updated_at cannot precede created_at")
        object.__setattr__(self, "constraints", tuple(self.constraints))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "claim_ids", tuple(self.claim_ids))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "verification_ids", tuple(self.verification_ids))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def digest(self) -> str:
        return _digest(
            {
                "research_item_id": self.research_item_id,
                "revision": self.revision,
                "title": self.title,
                "question": self.question,
                "research_class": self.research_class.value,
                "status": self.status.value,
                "project_id": self.project_id,
                "workspace_id": self.workspace_id,
                "task_id": self.task_id,
                "plan_id": self.plan_id,
                "run_id": self.run_id,
                "source_ids": list(self.source_ids),
                "claim_ids": list(self.claim_ids),
                "evidence_ids": list(self.evidence_ids),
                "verification_ids": list(self.verification_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class SourceRecord:
    research_item_id: str
    source_type: ResearchSourceType
    locator: str
    title: str
    source_id: str = field(default_factory=lambda: new_id("research_source"))
    author: str | None = None
    publisher: str | None = None
    license_ref: str | None = None
    trust_classification: str = "unclassified"
    current_observation_id: str | None = None
    observation_ids: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.source_id, "research_source")
        validate_id(self.research_item_id, "research_item")
        _nonblank(self.locator, "source locator")
        _nonblank(self.title, "source title")
        _nonblank(self.trust_classification, "source trust_classification")
        _optional_id(self.current_observation_id, "research_observation")
        for value in self.observation_ids:
            validate_id(value, "research_observation")
        if len(self.observation_ids) != len(set(self.observation_ids)):
            raise ValueError("source observation_ids must be unique")
        _aware(self.created_at, "source created_at")
        object.__setattr__(self, "observation_ids", tuple(self.observation_ids))
        object.__setattr__(self, "metadata", _metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class SourceObservation:
    source_id: str
    research_item_id: str
    retrieved_at: datetime
    observation_id: str = field(default_factory=lambda: new_id("research_observation"))
    state: SourceObservationState = SourceObservationState.CURRENT
    idempotency_key: str | None = None
    revision: str | None = None
    version: str | None = None
    commit: str | None = None
    etag: str | None = None
    content_digest: str | None = None
    snapshot_digest: str | None = None
    snapshot_artifact_id: str | None = None
    identity_proven: bool = False
    repository_id: str | None = None
    requested_repository_revision: str | None = None
    resolved_repository_revision: str | None = None
    intelligence_provider_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.observation_id, "research_observation")
        validate_id(self.source_id, "research_source")
        validate_id(self.research_item_id, "research_item")
        _aware(self.retrieved_at, "source observation retrieved_at")
        _optional_id(self.snapshot_artifact_id, "artifact")
        for name in (
            "idempotency_key",
            "revision",
            "version",
            "commit",
            "etag",
            "content_digest",
            "snapshot_digest",
            "repository_id",
            "requested_repository_revision",
            "resolved_repository_revision",
            "intelligence_provider_id",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonblank(value, f"source observation {name}")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def binding(self) -> tuple[str | None, ...]:
        return (
            self.revision,
            self.version,
            self.commit,
            self.etag,
            self.content_digest,
            self.snapshot_digest,
            self.resolved_repository_revision,
        )


@dataclass(frozen=True, slots=True)
class Claim:
    research_item_id: str
    text: str
    category: str
    claim_id: str = field(default_factory=lambda: new_id("research_claim"))
    revision: int = 1
    confidence: ClaimConfidence = ClaimConfidence.UNKNOWN
    status: ClaimStatus = ClaimStatus.PROPOSED
    evidence_ids: tuple[str, ...] = ()
    author_ref: str | None = None
    agent_id: str | None = None
    agent_revision: int | None = None
    run_id: str | None = None
    supersedes_claim_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.claim_id, "research_claim")
        validate_id(self.research_item_id, "research_item")
        _nonblank(self.text, "claim text")
        _nonblank(self.category, "claim category")
        if self.revision < 1:
            raise ValueError("claim revision must be >= 1")
        for value in self.evidence_ids:
            validate_id(value, "research_evidence")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence_ids must be unique")
        _optional_id(self.agent_id, "agent")
        _optional_id(self.run_id, "run")
        _optional_id(self.supersedes_claim_id, "research_claim")
        if self.agent_id is not None:
            if self.agent_revision is None or self.agent_revision < 1:
                raise ValueError("claim agent_id requires agent_revision >= 1")
        elif self.agent_revision is not None:
            raise ValueError("claim agent_revision requires agent_id")
        if self.author_ref is not None:
            _nonblank(self.author_ref, "claim author_ref")
        _aware(self.created_at, "claim created_at")
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def digest(self) -> str:
        return _digest(
            {
                "claim_id": self.claim_id,
                "revision": self.revision,
                "research_item_id": self.research_item_id,
                "text": self.text,
                "category": self.category,
                "confidence": self.confidence.value,
                "status": self.status.value,
                "evidence_ids": list(self.evidence_ids),
            }
        )


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    research_item_id: str
    source_id: str
    source_observation_id: str
    claim_id: str
    relation: EvidenceRelation
    retrieved_at: datetime
    evidence_id: str = field(default_factory=lambda: new_id("research_evidence"))
    task_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    agent_revision: int | None = None
    location_ref: str | None = None
    source_revision: str | None = None
    source_version: str | None = None
    source_commit: str | None = None
    source_etag: str | None = None
    source_content_digest: str | None = None
    source_snapshot_digest: str | None = None
    artifact_id: str | None = None
    excerpt_digest: str | None = None
    extraction_method: str = "manual"
    supersedes_evidence_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.evidence_id, "research_evidence")
        validate_id(self.research_item_id, "research_item")
        validate_id(self.source_id, "research_source")
        validate_id(self.source_observation_id, "research_observation")
        validate_id(self.claim_id, "research_claim")
        for value, prefix in (
            (self.task_id, "task"),
            (self.run_id, "run"),
            (self.agent_id, "agent"),
            (self.artifact_id, "artifact"),
            (self.supersedes_evidence_id, "research_evidence"),
        ):
            _optional_id(value, prefix)
        if self.agent_id is not None:
            if self.agent_revision is None or self.agent_revision < 1:
                raise ValueError("evidence agent_id requires agent_revision >= 1")
        elif self.agent_revision is not None:
            raise ValueError("evidence agent_revision requires agent_id")
        _nonblank(self.extraction_method, "evidence extraction_method")
        _aware(self.retrieved_at, "evidence retrieved_at")
        _aware(self.created_at, "evidence created_at")
        for name in (
            "location_ref",
            "source_revision",
            "source_version",
            "source_commit",
            "source_etag",
            "source_content_digest",
            "source_snapshot_digest",
            "excerpt_digest",
        ):
            value = getattr(self, name)
            if value is not None:
                _nonblank(value, f"evidence {name}")
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def digest(self) -> str:
        return _digest(
            {
                "evidence_id": self.evidence_id,
                "research_item_id": self.research_item_id,
                "source_id": self.source_id,
                "source_observation_id": self.source_observation_id,
                "claim_id": self.claim_id,
                "relation": self.relation.value,
                "retrieved_at": self.retrieved_at.isoformat(),
                "location_ref": self.location_ref,
                "source_revision": self.source_revision,
                "source_version": self.source_version,
                "source_commit": self.source_commit,
                "source_etag": self.source_etag,
                "source_content_digest": self.source_content_digest,
                "source_snapshot_digest": self.source_snapshot_digest,
                "artifact_id": self.artifact_id,
                "excerpt_digest": self.excerpt_digest,
                "supersedes_evidence_id": self.supersedes_evidence_id,
            }
        )


@dataclass(frozen=True, slots=True)
class ResearchVerificationBinding:
    research_item_id: str
    verification_id: str
    subject_type: ResearchVerificationSubjectType
    subject_id: str
    subject_revision: str
    subject_digest: str
    binding_id: str = field(default_factory=lambda: new_id("research_verification_binding"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.binding_id, "research_verification_binding")
        validate_id(self.research_item_id, "research_item")
        validate_id(self.verification_id, "verification")
        validate_id(self.subject_id, self.subject_type.value)
        _nonblank(self.subject_revision, "verification subject_revision")
        _nonblank(self.subject_digest, "verification subject_digest")
        _aware(self.created_at, "verification binding created_at")


@dataclass(frozen=True, slots=True)
class ResearchActionContext:
    research_item_id: str
    research_item_revision: int
    research_item_digest: str
    claim_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    verification_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        validate_id(self.research_item_id, "research_item")
        if self.research_item_revision < 1:
            raise ValueError("research_item_revision must be >= 1")
        _nonblank(self.research_item_digest, "research_item_digest")
        if not self.claim_ids or not self.evidence_ids:
            raise ValueError("research action context requires claims and evidence")
        for value in self.claim_ids:
            validate_id(value, "research_claim")
        for value in self.evidence_ids:
            validate_id(value, "research_evidence")
        for value in self.verification_ids:
            validate_id(value, "verification")

    def planning_evidence_refs(self) -> tuple[str, ...]:
        return (
            f"research-item:{self.research_item_id}@{self.research_item_revision}:{self.research_item_digest}",
            *(f"research-claim:{value}" for value in self.claim_ids),
            *(f"research-evidence:{value}" for value in self.evidence_ids),
            *(f"verification:{value}" for value in self.verification_ids),
        )


@dataclass(frozen=True, slots=True)
class UntrustedResearchExecutionProfile:
    """Policy projection consumed by existing Workspace/Executor/Auth boundaries."""

    read_only_source: bool = True
    isolated_workspace: bool = True
    allow_network_egress: bool = False
    allow_platform_secrets: bool = False
    allow_provider_secrets: bool = False
    allow_production_writes: bool = False
    cpu_limit: float = 1.0
    memory_limit_mb: int = 1024
    disk_limit_mb: int = 2048
    pids_limit: int = 128

    def __post_init__(self) -> None:
        if not self.read_only_source or not self.isolated_workspace:
            raise ValueError("untrusted research requires read-only source and isolated workspace")
        if self.allow_platform_secrets or self.allow_provider_secrets:
            raise ValueError("untrusted research execution cannot receive protected secrets")
        if self.allow_production_writes:
            raise ValueError("untrusted research execution cannot receive production write scope")
        if self.cpu_limit <= 0:
            raise ValueError("cpu_limit must be greater than zero")
        if self.memory_limit_mb <= 0 or self.disk_limit_mb <= 0 or self.pids_limit <= 0:
            raise ValueError("resource limits must be greater than zero")
