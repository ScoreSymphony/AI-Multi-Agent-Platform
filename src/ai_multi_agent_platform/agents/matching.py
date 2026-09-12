"""Provider-neutral canonical Agent/AgentTeam matching (#903).

The matcher decides only which exact canonical Agent/AgentTeam revision is eligible for work.
Worker placement, model routing, capability-provider selection and authorization remain owned by
#14, #10, #12 and #15 respectively. Callers may inject an authorization policy filter; discovery
never implies permission to execute a candidate.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypeAlias

from ai_multi_agent_platform.capabilities import CapabilityRegistry, CapabilitySpec
from ai_multi_agent_platform.contracts.types import HealthStatus
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelLocation, ModelRegistry, RoutingRequirements

from .models import (
    AgentRevision,
    AgentRevisionRef,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    CapabilityConstraint,
)
from .repository import AgentRepository

AgentParticipantRef: TypeAlias = AgentRevisionRef | AgentTeamRevisionRef


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
    policy_refs: tuple[str, ...] = ()
    model_requirements: tuple[RoutingRequirements, ...] = ()
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
    """Narrow #15-style eligibility hook; implementations must fail closed."""

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


class AgentMatcher:
    """Deterministic, explainable matcher over canonical candidate snapshots."""

    def __init__(
        self,
        *,
        capability_specs: Iterable[CapabilitySpec] = (),
        model_registry: ModelRegistry | None = None,
        policy: AgentMatchingPolicy | None = None,
    ) -> None:
        self._capabilities = tuple(capability_specs)
        self._models = model_registry
        self._policy = policy

    @classmethod
    def from_capability_registry(
        cls,
        registry: CapabilityRegistry,
        *,
        model_registry: ModelRegistry | None = None,
        policy: AgentMatchingPolicy | None = None,
    ) -> "AgentMatcher":
        return cls(
            capability_specs=registry.inventory_capabilities(include_unavailable=False),
            model_registry=model_registry,
            policy=policy,
        )

    def match(
        self,
        requirements: AgentMatchingRequirements,
        candidates: Iterable[AgentMatchCandidate],
    ) -> AgentMatchResult:
        materialized = tuple(candidates)
        exact = requirements.exact_agent or requirements.exact_team
        if exact is not None:
            pinned = tuple(candidate for candidate in materialized if candidate.ref == exact)
            if not pinned:
                return AgentMatchResult(
                    status=AgentMatchStatus.NO_MATCH,
                    selected=None,
                    outcomes=(),
                )
            outcome = self.evaluate(requirements, pinned[0])
            return AgentMatchResult(
                status=(
                    AgentMatchStatus.SELECTED if outcome.eligible else AgentMatchStatus.NO_MATCH
                ),
                selected=(pinned[0].ref if outcome.eligible else None),
                outcomes=(outcome,),
            )

        outcomes = tuple(self.evaluate(requirements, candidate) for candidate in materialized)
        eligible = [outcome for outcome in outcomes if outcome.eligible]
        if not eligible:
            return AgentMatchResult(
                status=AgentMatchStatus.NO_MATCH,
                selected=None,
                outcomes=outcomes,
            )
        eligible.sort(
            key=lambda outcome: (outcome.score, _candidate_sort_key(outcome.candidate)),
            reverse=True,
        )
        best_score = eligible[0].score
        best = [outcome for outcome in eligible if outcome.score == best_score]
        if len(best) == 1:
            return AgentMatchResult(
                status=AgentMatchStatus.SELECTED,
                selected=best[0].candidate.ref,
                outcomes=outcomes,
            )
        if requirements.tie_break is AgentTieBreakPolicy.CANONICAL_ID:
            selected = min(best, key=lambda item: _candidate_sort_key(item.candidate))
            return AgentMatchResult(
                status=AgentMatchStatus.SELECTED,
                selected=selected.candidate.ref,
                outcomes=outcomes,
                ambiguous=tuple(item.candidate.ref for item in best),
            )
        return AgentMatchResult(
            status=AgentMatchStatus.AMBIGUOUS,
            selected=None,
            outcomes=outcomes,
            ambiguous=tuple(
                item.candidate.ref
                for item in sorted(best, key=lambda item: _candidate_sort_key(item.candidate))
            ),
        )

    def evaluate(
        self,
        requirements: AgentMatchingRequirements,
        candidate: AgentMatchCandidate,
    ) -> AgentMatchCandidateOutcome:
        rejections: list[AgentMatchRejection] = []
        rationale: list[str] = []

        if candidate.kind not in requirements.candidate_kinds:
            rejections.append(
                AgentMatchRejection(AgentMatchReason.UNSUPPORTED_KIND, "candidate kind not allowed")
            )
        if not candidate.enabled:
            rejections.append(
                AgentMatchRejection(AgentMatchReason.DISABLED, "candidate is disabled")
            )
        if not _scope_compatible(candidate, requirements):
            rejections.append(
                AgentMatchRejection(AgentMatchReason.WRONG_SCOPE, "candidate scope is incompatible")
            )
        if requirements.required_roles and not set(requirements.required_roles).issubset(
            candidate.roles
        ):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.ROLE_MISMATCH,
                    "candidate does not provide every required role",
                )
            )
        if _independence_conflict(candidate, requirements.excluded_participants):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.INDEPENDENCE_VIOLATION,
                    "candidate violates supplied producer/reviewer independence",
                )
            )

        for capability_id in requirements.forbidden_capability_ids:
            if _candidate_exposes_capability(candidate, capability_id):
                rejections.append(
                    AgentMatchRejection(
                        AgentMatchReason.DENIED_CAPABILITY,
                        f"candidate exposes forbidden capability {capability_id}",
                    )
                )

        for requirement in requirements.required_capabilities:
            rejection = self._capability_rejection(candidate, requirement)
            if rejection is not None:
                rejections.append(rejection)

        missing_policies = set(requirements.required_policy_refs) - set(candidate.policy_refs)
        if missing_policies:
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.POLICY_DENIED,
                    "candidate does not satisfy required policy references",
                )
            )

        if not self._model_feasible(candidate, requirements.model_requirements):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE,
                    "candidate and work model requirements have no compatible canonical model",
                )
            )

        if self._policy is not None and not self._policy.allows(candidate, requirements):
            rejections.append(
                AgentMatchRejection(
                    AgentMatchReason.POLICY_DENIED,
                    "candidate denied by canonical policy",
                )
            )

        if rejections:
            return AgentMatchCandidateOutcome(
                candidate=candidate,
                eligible=False,
                rejections=tuple(rejections),
            )

        preferred_id = int(
            (
                isinstance(candidate.ref, AgentRevisionRef)
                and candidate.ref.agent_id in requirements.preferred_agent_ids
            )
            or (
                isinstance(candidate.ref, AgentTeamRevisionRef)
                and candidate.ref.team_id in requirements.preferred_team_ids
            )
        )
        preferred_roles = len(set(candidate.roles).intersection(requirements.preferred_roles))
        excess_privilege = max(
            0,
            len(_declared_capability_ids(candidate)) - len(requirements.required_capabilities),
        )
        score = (
            preferred_id,
            preferred_roles,
            candidate.matching_priority,
            -excess_privilege,
        )
        rationale.extend(
            (
                "all mandatory eligibility constraints satisfied",
                f"preferred-role matches={preferred_roles}",
                f"configured matching priority={candidate.matching_priority}",
                f"excess declared capabilities={excess_privilege}",
            )
        )
        return AgentMatchCandidateOutcome(
            candidate=candidate,
            eligible=True,
            score=score,
            rationale=tuple(rationale),
        )

    def _capability_rejection(
        self,
        candidate: AgentMatchCandidate,
        requirement: AgentCapabilityRequirement,
    ) -> AgentMatchRejection | None:
        capability_id = requirement.capability_id
        if capability_id in candidate.denied_capability_ids:
            return AgentMatchRejection(
                AgentMatchReason.DENIED_CAPABILITY,
                f"required capability {capability_id} is explicitly denied",
            )
        declared = _declared_capability_ids(candidate)
        if capability_id not in declared:
            return AgentMatchRejection(
                AgentMatchReason.MISSING_CAPABILITY,
                f"required capability {capability_id} is not assigned to candidate",
            )

        candidate_constraint = next(
            (
                item
                for item in candidate.capability_constraints
                if item.capability_id == capability_id
            ),
            None,
        )
        if not self._capabilities:
            if candidate_constraint is not None and not _constraints_can_overlap(
                requirement,
                candidate_constraint,
            ):
                return AgentMatchRejection(
                    AgentMatchReason.CAPABILITY_VERSION_INCOMPATIBLE,
                    f"required capability {capability_id} conflicts with candidate constraint",
                )
            return None

        canonical_specs = tuple(
            spec
            for spec in self._capabilities
            if spec.capability_id == capability_id
            and spec.available
            and spec.health is not HealthStatus.UNAVAILABLE
        )
        if not canonical_specs:
            return AgentMatchRejection(
                AgentMatchReason.MISSING_CAPABILITY,
                f"required capability {capability_id} has no available canonical version",
            )
        compatible = [
            spec
            for spec in canonical_specs
            if _capability_requirement_matches(requirement, spec)
            and (
                candidate_constraint is None
                or _capability_constraint_matches(candidate_constraint, spec)
            )
        ]
        if not compatible:
            return AgentMatchRejection(
                AgentMatchReason.CAPABILITY_VERSION_INCOMPATIBLE,
                f"required capability {capability_id} has no compatible canonical version",
            )
        return None

    def _model_feasible(
        self,
        candidate: AgentMatchCandidate,
        work: RoutingRequirements,
    ) -> bool:
        candidate_requirements = candidate.model_requirements or (RoutingRequirements(),)
        merged = tuple(
            value
            for value in (_merge_model_requirements(item, work) for item in candidate_requirements)
            if value is not None
        )
        if not merged:
            return False
        if self._models is None:
            return True
        models = self._models.list_models(enabled=True)
        return any(
            any(_model_matches(config, requirement, self._models) for config in models)
            for requirement in merged
        )


class AgentResolver:
    """Repository-backed application service resolving exact immutable Agent/Team revisions."""

    def __init__(
        self,
        repository: AgentRepository,
        *,
        matcher: AgentMatcher | None = None,
        capability_registry: CapabilityRegistry | None = None,
        model_registry: ModelRegistry | None = None,
        policy: AgentMatchingPolicy | None = None,
    ) -> None:
        self.repository = repository
        if matcher is not None and any(
            value is not None for value in (capability_registry, model_registry, policy)
        ):
            raise ValueError("explicit matcher cannot be combined with matcher dependencies")
        if matcher is not None:
            self.matcher = matcher
        elif capability_registry is not None:
            self.matcher = AgentMatcher.from_capability_registry(
                capability_registry,
                model_registry=model_registry,
                policy=policy,
            )
        else:
            self.matcher = AgentMatcher(model_registry=model_registry, policy=policy)

    def resolve(self, requirements: AgentMatchingRequirements) -> AgentMatchResult:
        return self.matcher.match(requirements, self._candidates(requirements))

    def _candidates(
        self,
        requirements: AgentMatchingRequirements,
    ) -> tuple[AgentMatchCandidate, ...]:
        if requirements.exact_agent is not None:
            revision = self.repository.get_agent_revision(
                requirements.exact_agent.agent_id,
                requirements.exact_agent.revision,
            )
            return (_agent_candidate(revision),)
        if requirements.exact_team is not None:
            revision = self.repository.get_team_revision(
                requirements.exact_team.team_id,
                requirements.exact_team.revision,
            )
            return (_team_candidate(revision, self.repository),)

        candidates: list[AgentMatchCandidate] = []
        if AgentCandidateKind.AGENT in requirements.candidate_kinds:
            for definition in self.repository.list_agents():
                candidates.append(
                    _agent_candidate(
                        self.repository.get_agent_revision(
                            definition.agent_id,
                            definition.current_revision,
                        )
                    )
                )
        if AgentCandidateKind.TEAM in requirements.candidate_kinds:
            for definition in self.repository.list_teams():
                candidates.append(
                    _team_candidate(
                        self.repository.get_team_revision(
                            definition.team_id,
                            definition.current_revision,
                        ),
                        self.repository,
                    )
                )
        return tuple(candidates)


def _agent_candidate(revision: AgentRevision) -> AgentMatchCandidate:
    profile = revision.profile
    policies = list(profile.policy_hooks.verification_policy_refs)
    if profile.policy_hooks.authorization_profile_ref is not None:
        policies.append(profile.policy_hooks.authorization_profile_ref)
    priority = _matching_priority(profile.metadata)
    return AgentMatchCandidate(
        ref=AgentRevisionRef(revision.agent_id, revision.revision),
        kind=AgentCandidateKind.AGENT,
        roles=(profile.role,),
        enabled=profile.enabled,
        owner_ref=revision.owner_ref,
        project_id=revision.project_id,
        workspace_id=revision.workspace_id,
        allowed_capability_ids=profile.capabilities.allowed,
        denied_capability_ids=profile.capabilities.denied,
        capability_constraints=profile.capabilities.constraints,
        policy_refs=tuple(dict.fromkeys(policies)),
        model_requirements=(profile.model.requirements,),
        matching_priority=priority,
    )


def _team_candidate(
    revision: AgentTeamRevision,
    repository: AgentRepository,
) -> AgentMatchCandidate:
    member_revisions = tuple(
        repository.get_agent_revision(member.agent.agent_id, member.agent.revision)
        for member in revision.profile.members
    )
    required_members = tuple(
        member_revision
        for member, member_revision in zip(
            revision.profile.members,
            member_revisions,
            strict=True,
        )
        if member.required
    )
    roles = tuple(
        dict.fromkeys(
            [member.role for member in revision.profile.members]
            + [member.profile.role for member in member_revisions]
        )
    )
    allowed = set(revision.profile.shared_capability_ids)
    denied: set[str] = set()
    constraints: dict[str, CapabilityConstraint] = {}
    policies: set[str] = set()
    enabled = revision.profile.enabled
    if revision.profile.coordination_policy_ref is not None:
        policies.add(revision.profile.coordination_policy_ref)
    for member in member_revisions:
        enabled = enabled and member.profile.enabled
        allowed.update(member.profile.capabilities.allowed)
        allowed.update(member.profile.capabilities.required_ids)
        denied.update(member.profile.capabilities.denied)
        for constraint in member.profile.capabilities.constraints:
            constraints.setdefault(constraint.capability_id, constraint)
        policies.update(member.profile.policy_hooks.verification_policy_refs)
        if member.profile.policy_hooks.authorization_profile_ref is not None:
            policies.add(member.profile.policy_hooks.authorization_profile_ref)
    priority = _matching_priority(revision.profile.metadata)
    return AgentMatchCandidate(
        ref=AgentTeamRevisionRef(revision.team_id, revision.revision),
        kind=AgentCandidateKind.TEAM,
        roles=roles,
        enabled=enabled,
        owner_ref=revision.owner_ref,
        project_id=revision.project_id,
        workspace_id=revision.workspace_id,
        allowed_capability_ids=tuple(sorted(allowed)),
        denied_capability_ids=tuple(sorted(denied)),
        capability_constraints=tuple(constraints[key] for key in sorted(constraints)),
        policy_refs=tuple(sorted(policies)),
        model_requirements=tuple(member.profile.model.requirements for member in required_members),
        member_refs=tuple(
            AgentRevisionRef(member.agent_id, member.revision) for member in member_revisions
        ),
        matching_priority=priority,
    )


def _matching_priority(metadata: object) -> int:
    if not hasattr(metadata, "get"):
        return 0
    value = metadata.get("matching_priority")  # type: ignore[union-attr]
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _scope_compatible(
    candidate: AgentMatchCandidate,
    requirements: AgentMatchingRequirements,
) -> bool:
    if candidate.project_id is not None and candidate.project_id != requirements.project_id:
        return False
    if candidate.workspace_id is not None and candidate.workspace_id != requirements.workspace_id:
        return False
    if requirements.organization_id is not None:
        if candidate.owner_ref.type == "organization":
            return candidate.owner_ref.id == requirements.organization_id
        if candidate.owner_ref.type == "team":
            return True
    return True


def _independence_conflict(
    candidate: AgentMatchCandidate,
    excluded: tuple[AgentParticipantRef, ...],
) -> bool:
    excluded_set = set(excluded)
    if candidate.ref in excluded_set:
        return True
    excluded_agents = {item for item in excluded if isinstance(item, AgentRevisionRef)}
    return bool(excluded_agents.intersection(candidate.member_refs))


def _candidate_exposes_capability(candidate: AgentMatchCandidate, capability_id: str) -> bool:
    return capability_id in _declared_capability_ids(candidate)


def _declared_capability_ids(candidate: AgentMatchCandidate) -> set[str]:
    return set(candidate.allowed_capability_ids).union(
        item.capability_id for item in candidate.capability_constraints
    )


def _capability_requirement_matches(
    requirement: AgentCapabilityRequirement,
    spec: CapabilitySpec,
) -> bool:
    if requirement.exact_version is not None and spec.version != requirement.exact_version:
        return False
    if requirement.minimum_version is not None or requirement.maximum_version is not None:
        key = _numeric_version_key(spec.version)
        if key is None:
            return False
        if requirement.minimum_version is not None:
            minimum = _numeric_version_key(requirement.minimum_version)
            if minimum is None:
                return False
            if key < minimum or (key == minimum and not requirement.include_minimum):
                return False
        if requirement.maximum_version is not None:
            maximum = _numeric_version_key(requirement.maximum_version)
            if maximum is None:
                return False
            if key > maximum or (key == maximum and not requirement.include_maximum):
                return False
    return set(requirement.required_features).issubset(spec.features)


def _capability_constraint_matches(
    constraint: CapabilityConstraint,
    spec: CapabilitySpec,
) -> bool:
    requirement = AgentCapabilityRequirement(
        capability_id=constraint.capability_id,
        exact_version=constraint.exact_version,
        minimum_version=constraint.minimum_version,
        maximum_version=constraint.maximum_version,
        include_maximum=True,
        required_features=constraint.required_features,
    )
    return _capability_requirement_matches(requirement, spec)


def _constraints_can_overlap(
    requirement: AgentCapabilityRequirement,
    constraint: CapabilityConstraint,
) -> bool:
    if requirement.exact_version is not None:
        if constraint.exact_version is not None:
            return requirement.exact_version == constraint.exact_version
        return _version_in_constraint(requirement.exact_version, constraint)
    if constraint.exact_version is not None:
        return _version_in_requirement(constraint.exact_version, requirement)
    request_min = _numeric_version_key(requirement.minimum_version)
    request_max = _numeric_version_key(requirement.maximum_version)
    candidate_min = _numeric_version_key(constraint.minimum_version)
    candidate_max = _numeric_version_key(constraint.maximum_version)
    if any(
        raw is not None and parsed is None
        for raw, parsed in (
            (requirement.minimum_version, request_min),
            (requirement.maximum_version, request_max),
            (constraint.minimum_version, candidate_min),
            (constraint.maximum_version, candidate_max),
        )
    ):
        return False
    lower = (
        max(value for value in (request_min, candidate_min) if value is not None)
        if any(value is not None for value in (request_min, candidate_min))
        else None
    )
    upper = (
        min(value for value in (request_max, candidate_max) if value is not None)
        if any(value is not None for value in (request_max, candidate_max))
        else None
    )
    return lower is None or upper is None or lower <= upper


def _version_in_constraint(version: str, constraint: CapabilityConstraint) -> bool:
    requirement = AgentCapabilityRequirement(
        capability_id=constraint.capability_id,
        minimum_version=constraint.minimum_version,
        maximum_version=constraint.maximum_version,
        include_maximum=True,
    )
    return _version_in_requirement(version, requirement)


def _version_in_requirement(version: str, requirement: AgentCapabilityRequirement) -> bool:
    pseudo = _CapabilityVersion(version=version)
    return _capability_requirement_matches(requirement, pseudo)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class _CapabilityVersion:
    version: str
    features: tuple[str, ...] = ()


def _numeric_version_key(value: str | None) -> tuple[int, int, int] | None:
    if value is None:
        return None
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        return None
    normalized = [int(part) for part in parts]
    normalized.extend([0] * (3 - len(normalized)))
    return tuple(normalized)  # type: ignore[return-value]


def _merge_model_requirements(
    first: RoutingRequirements,
    second: RoutingRequirements,
) -> RoutingRequirements | None:
    explicit = first.explicit_model_id or second.explicit_model_id
    if (
        first.explicit_model_id is not None
        and second.explicit_model_id is not None
        and first.explicit_model_id != second.explicit_model_id
    ):
        return None
    local_only = first.local_only or second.local_only
    self_hosted_only = (first.self_hosted_only or second.self_hosted_only) and not local_only
    return RoutingRequirements(
        explicit_model_id=explicit,
        min_context_window=max(
            value for value in (first.min_context_window, second.min_context_window, 0)
        )
        or None,
        tool_calling=first.tool_calling or second.tool_calling,
        structured_output=first.structured_output or second.structured_output,
        streaming=first.streaming or second.streaming,
        modalities=tuple(dict.fromkeys((*first.modalities, *second.modalities))),
        reasoning=tuple(dict.fromkeys((*first.reasoning, *second.reasoning))),
        local_only=local_only,
        self_hosted_only=self_hosted_only,
    )


def _model_matches(
    config: object,
    requirements: RoutingRequirements,
    registry: ModelRegistry,
) -> bool:
    if not hasattr(config, "config_id"):
        return False
    model = config
    if not model.enabled:  # type: ignore[union-attr]
        return False
    if registry.effective_health(model) is HealthStatus.UNAVAILABLE:  # type: ignore[arg-type]
        return False
    if requirements.explicit_model_id is not None and (
        model.config_id != requirements.explicit_model_id  # type: ignore[union-attr]
        and requirements.explicit_model_id not in model.aliases  # type: ignore[union-attr]
    ):
        return False
    location = model.location  # type: ignore[union-attr]
    if requirements.local_only and location is not ModelLocation.LOCAL:
        return False
    if requirements.self_hosted_only and location not in {
        ModelLocation.LOCAL,
        ModelLocation.SELF_HOSTED,
    }:
        return False
    capabilities = model.capabilities  # type: ignore[union-attr]
    if requirements.min_context_window is not None and (
        capabilities.context_window is None
        or capabilities.context_window < requirements.min_context_window
    ):
        return False
    if requirements.tool_calling and not capabilities.tool_calling:
        return False
    if requirements.structured_output and not capabilities.structured_output:
        return False
    if requirements.streaming and not capabilities.streaming:
        return False
    if requirements.modalities and not set(requirements.modalities).issubset(
        capabilities.modalities
    ):
        return False
    if requirements.reasoning and not set(requirements.reasoning).issubset(capabilities.reasoning):
        return False
    return True


def _candidate_sort_key(candidate: AgentMatchCandidate) -> tuple[str, str, int]:
    return candidate.participant_key


__all__ = [
    "AgentCandidateKind",
    "AgentCapabilityRequirement",
    "AgentMatchCandidate",
    "AgentMatchCandidateOutcome",
    "AgentMatchReason",
    "AgentMatchRejection",
    "AgentMatchResult",
    "AgentMatchStatus",
    "AgentMatcher",
    "AgentMatchingPolicy",
    "AgentMatchingRequirements",
    "AgentParticipantRef",
    "AgentResolver",
    "AgentTieBreakPolicy",
    "CallableAgentMatchingPolicy",
]
