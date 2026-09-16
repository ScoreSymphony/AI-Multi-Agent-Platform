"""Canonical contracts and diagnostics for Agent and AgentTeam matching."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import RoutingRequirements

from .models import (
    AgentRevisionRef,
    AgentTeamRevisionRef,
    CapabilityConstraint,
)

type AgentParticipantRef = AgentRevisionRef | AgentTeamRevisionRef


class AgentCandidateKind(StrEnum):
    AGENT = "agent"
    TEAM = "team"


class AgentMatchStatus(StrEnum):
    SELECTED = "selected"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"


class AgentMatchReason(StrEnum):
    ELIGIBLE = "eligible"
    DISABLED = "disabled"
    WRONG_SCOPE = "wrong_scope"
    ROLE_MISMATCH = "role_mismatch"
    MISSING_CAPABILITY = "missing_capability"
    DENIED_CAPABILITY = "denied_capability"
    CAPABILITY_VERSION_INCOMPATIBLE = "capability_version_incompatible"
    MODEL_REQUIREMENT_INCOMPATIBLE = "model_requirement_incompatible"
    MODEL_OVERRIDE_FORBIDDEN = "model_override_forbidden"
    POLICY_DENIED = "policy_denied"
    INDEPENDENCE_VIOLATION = "independence_violation"
    PIN_MISMATCH = "pin_mismatch"
    UNSUPPORTED_KIND = "unsupported_kind"


class AgentTieBreakPolicy(StrEnum):
    AMBIGUOUS = "ambiguous"
    CANONICAL_ID = "canonical_id"


@dataclass(frozen=True, slots=True)
class AgentCapabilityRequirement:
    capability_id: str
    exact_version: str | None = None
    minimum_version: str | None = None
    maximum_version: str | None = None
    include_minimum: bool = True
    include_maximum: bool = False
    required_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise ValueError("capability_id must not be blank")
        if self.exact_version is not None and not self.exact_version.strip():
            raise ValueError("exact_version must not be blank")
        if self.minimum_version is not None and not self.minimum_version.strip():
            raise ValueError("minimum_version must not be blank")
        if self.maximum_version is not None and not self.maximum_version.strip():
            raise ValueError("maximum_version must not be blank")
        if self.exact_version is not None and (
            self.minimum_version is not None or self.maximum_version is not None
        ):
            raise ValueError("exact capability version cannot be combined with a version range")
        if any(not value.strip() for value in self.required_features):
            raise ValueError("required capability features must not be blank")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required capability features must be unique")


@dataclass(frozen=True, slots=True)
class AgentMatchingRequirements:
    required_roles: tuple[str, ...] = ()
    preferred_roles: tuple[str, ...] = ()
    required_capabilities: tuple[AgentCapabilityRequirement, ...] = ()
    forbidden_capability_ids: tuple[str, ...] = ()
    required_policy_refs: tuple[str, ...] = ()
    model_requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    model_requirements_are_task_override: bool = False
    project_id: str | None = None
    workspace_id: str | None = None
    organization_id: str | None = None
    exact_agent: AgentRevisionRef | None = None
    exact_team: AgentTeamRevisionRef | None = None
    excluded_participants: tuple[AgentParticipantRef, ...] = ()
    preferred_agent_ids: tuple[str, ...] = ()
    preferred_team_ids: tuple[str, ...] = ()
    candidate_kinds: tuple[AgentCandidateKind, ...] = (
        AgentCandidateKind.AGENT,
        AgentCandidateKind.TEAM,
    )
    tie_break: AgentTieBreakPolicy = AgentTieBreakPolicy.AMBIGUOUS

    def __post_init__(self) -> None:
        if self.exact_agent is not None and self.exact_team is not None:
            raise ValueError("matching requirements cannot pin both an Agent and Agent Team")
        for values, name in (
            (self.required_roles, "required_roles"),
            (self.preferred_roles, "preferred_roles"),
            (self.forbidden_capability_ids, "forbidden_capability_ids"),
            (self.required_policy_refs, "required_policy_refs"),
            (self.preferred_agent_ids, "preferred_agent_ids"),
            (self.preferred_team_ids, "preferred_team_ids"),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"{name} must not contain blank values")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must contain unique values")
        if not self.candidate_kinds:
            raise ValueError("candidate_kinds must not be empty")
        if len(set(self.candidate_kinds)) != len(self.candidate_kinds):
            raise ValueError("candidate_kinds must be unique")


@dataclass(frozen=True, slots=True)
class AgentMatchRejection:
    reason: AgentMatchReason
    detail: str


@dataclass(frozen=True, slots=True)
class AgentMatchCandidate:
    ref: AgentParticipantRef
    kind: AgentCandidateKind
    roles: tuple[str, ...]
    enabled: bool
    owner_ref: OwnerRef
    project_id: str | None = None
    workspace_id: str | None = None
    allowed_capability_ids: tuple[str, ...] = ()
    denied_capability_ids: tuple[str, ...] = ()
    capability_constraints: tuple[CapabilityConstraint, ...] = ()
    capabilities_unrestricted: bool = False
    policy_refs: tuple[str, ...] = ()
    model_requirements: tuple[RoutingRequirements, ...] = ()
    model_requirements_valid: bool = True
    allows_task_model_override: bool = True
    member_refs: tuple[AgentRevisionRef, ...] = ()
    matching_priority: int = 0

    @property
    def participant_key(self) -> tuple[str, str, int]:
        if isinstance(self.ref, AgentRevisionRef):
            return ("agent", self.ref.agent_id, self.ref.revision)
        return ("team", self.ref.team_id, self.ref.revision)

    @property
    def display_ref(self) -> str:
        kind, resource_id, revision = self.participant_key
        return f"{kind}:{resource_id}@{revision}"


@dataclass(frozen=True, slots=True)
class AgentMatchCandidateOutcome:
    candidate: AgentMatchCandidate
    eligible: bool
    score: tuple[int, int, int, int] = (0, 0, 0, 0)
    rejections: tuple[AgentMatchRejection, ...] = ()
    rationale: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentMatchResult:
    status: AgentMatchStatus
    selected: AgentParticipantRef | None
    outcomes: tuple[AgentMatchCandidateOutcome, ...]
    ambiguous: tuple[AgentParticipantRef, ...] = ()

    @property
    def selected_outcome(self) -> AgentMatchCandidateOutcome | None:
        if self.selected is None:
            return None
        for outcome in self.outcomes:
            if outcome.candidate.ref == self.selected:
                return outcome
        return None


class AgentMatchingPolicy(Protocol):
    """Narrow eligibility hook; implementations must fail closed."""

    def allows(
        self,
        candidate: AgentMatchCandidate,
        requirements: AgentMatchingRequirements,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class CallableAgentMatchingPolicy:
    callback: Callable[[AgentMatchCandidate, AgentMatchingRequirements], bool]

    def allows(
        self,
        candidate: AgentMatchCandidate,
        requirements: AgentMatchingRequirements,
    ) -> bool:
        return self.callback(candidate, requirements)


__all__ = [
    "AgentCandidateKind",
    "AgentCapabilityRequirement",
    "AgentMatchCandidate",
    "AgentMatchCandidateOutcome",
    "AgentMatchReason",
    "AgentMatchRejection",
    "AgentMatchResult",
    "AgentMatchStatus",
    "AgentMatchingPolicy",
    "AgentMatchingRequirements",
    "AgentParticipantRef",
    "AgentTieBreakPolicy",
    "CallableAgentMatchingPolicy",
]
