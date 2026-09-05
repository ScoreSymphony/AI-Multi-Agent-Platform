"""Canonical durable capability-assignment policy models for issue #366."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

CAPABILITY_ASSIGNMENT_SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


class CapabilityAssignmentTargetType(StrEnum):
    """Canonical subjects that reusable capability policy may target initially."""

    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    PROJECT = "project"


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentTarget:
    """Exact canonical subject receiving one assignment policy revision."""

    subject_type: CapabilityAssignmentTargetType
    subject_id: str

    def __post_init__(self) -> None:
        prefix = {
            CapabilityAssignmentTargetType.AGENT: "agent",
            CapabilityAssignmentTargetType.AGENT_TEAM: "team",
            CapabilityAssignmentTargetType.PROJECT: "project",
        }[self.subject_type]
        validate_id(self.subject_id, prefix)


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentRule:
    """Provider-neutral capability reference plus optional compatibility intent."""

    capability_id: str
    exact_version: str | None = None
    compatibility: CapabilityCompatibilityRequest | None = None
    privileged: bool = False
    approval_required: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        if self.exact_version is not None:
            _require_nonblank(self.exact_version, "exact_version")
        if self.exact_version is not None and self.compatibility is not None:
            raise ValueError("exact_version and compatibility are mutually exclusive")


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentProvenance:
    """Minimal provenance deliberately excluding arbitrary/private runtime metadata."""

    source: str
    creator_ref: str

    def __post_init__(self) -> None:
        _require_nonblank(self.source, "assignment source")
        _require_nonblank(self.creator_ref, "assignment creator_ref")


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentContent:
    """Immutable policy content for one revision.

    Conflict semantics are intentionally deterministic: a capability ID can appear in
    exactly one of required/allowed/denied within a revision. Denied therefore cannot
    silently override or be overridden by another list.
    """

    target: CapabilityAssignmentTarget
    required: tuple[CapabilityAssignmentRule, ...] = ()
    allowed: tuple[CapabilityAssignmentRule, ...] = ()
    denied: tuple[CapabilityAssignmentRule, ...] = ()
    provenance: CapabilityAssignmentProvenance = field(
        default_factory=lambda: CapabilityAssignmentProvenance(
            source="local",
            creator_ref="local",
        )
    )
    schema_version: str = CAPABILITY_ASSIGNMENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_nonblank(self.schema_version, "schema_version")
        groups = {
            "required": self.required,
            "allowed": self.allowed,
            "denied": self.denied,
        }
        ids_by_group: dict[str, set[str]] = {}
        for name, rules in groups.items():
            ids = [rule.capability_id for rule in rules]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} capability IDs must be unique")
            ids_by_group[name] = set(ids)
        overlaps = (
            ids_by_group["required"].intersection(ids_by_group["allowed"])
            | ids_by_group["required"].intersection(ids_by_group["denied"])
            | ids_by_group["allowed"].intersection(ids_by_group["denied"])
        )
        if overlaps:
            raise ValueError(
                "required/allowed/denied capability sets must be disjoint: "
                + ", ".join(sorted(overlaps))
            )

    @property
    def all_rules(self) -> tuple[CapabilityAssignmentRule, ...]:
        return self.required + self.allowed + self.denied


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentRevision:
    """One immutable historical snapshot of a canonical assignment policy."""

    assignment_id: str
    revision: int
    owner_ref: OwnerRef
    content: CapabilityAssignmentContent
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.assignment_id, "cap_assignment")
        if self.revision < 1:
            raise ValueError("capability assignment revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")


@dataclass(frozen=True, slots=True)
class CapabilityAssignmentPolicy:
    """Stable canonical identity pointing at the latest immutable revision."""

    owner_ref: OwnerRef
    current_revision: int
    assignment_id: str = field(default_factory=lambda: new_id("cap_assignment"))
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.assignment_id, "cap_assignment")
        if self.current_revision < 1:
            raise ValueError("capability assignment current_revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
