"""Repository-backed candidate materialization for canonical Agent matching."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import ModelRegistry, RoutingRequirements

from .matching_contracts import (
    AgentCandidateKind,
    AgentMatchCandidate,
    AgentMatchingPolicy,
    AgentMatchingRequirements,
    AgentMatchResult,
)
from .matching_engine import AgentMatcher, _merge_model_requirements
from .models import (
    AgentRevision,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamRevision,
    AgentTeamRevisionRef,
    CapabilityConstraint,
    UnavailableMemberPolicy,
)
from .repository import AgentRepository


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
        routing_profiles: Mapping[str, RoutingRequirements] | None = None,
    ) -> None:
        self.repository = repository
        self.routing_profiles = dict(routing_profiles or {})
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
            agent_revision = self.repository.get_agent_revision(
                requirements.exact_agent.agent_id,
                requirements.exact_agent.revision,
            )
            return (_agent_candidate(agent_revision, self.routing_profiles),)
        if requirements.exact_team is not None:
            team_revision = self.repository.get_team_revision(
                requirements.exact_team.team_id,
                requirements.exact_team.revision,
            )
            return (_team_candidate(team_revision, self.repository, self.routing_profiles),)

        candidates: list[AgentMatchCandidate] = []
        if AgentCandidateKind.AGENT in requirements.candidate_kinds:
            for agent_definition in self.repository.list_agents():
                candidates.append(
                    _agent_candidate(
                        self.repository.get_agent_revision(
                            agent_definition.agent_id,
                            agent_definition.current_revision,
                        ),
                        self.routing_profiles,
                    )
                )
        if AgentCandidateKind.TEAM in requirements.candidate_kinds:
            for team_definition in self.repository.list_teams():
                candidates.append(
                    _team_candidate(
                        self.repository.get_team_revision(
                            team_definition.team_id,
                            team_definition.current_revision,
                        ),
                        self.repository,
                        self.routing_profiles,
                    )
                )
        return tuple(candidates)


def _agent_candidate(
    revision: AgentRevision,
    routing_profiles: Mapping[str, RoutingRequirements] | None = None,
) -> AgentMatchCandidate:
    profile = revision.profile
    policies = list(profile.policy_hooks.verification_policy_refs)
    if profile.policy_hooks.authorization_profile_ref is not None:
        policies.append(profile.policy_hooks.authorization_profile_ref)
    priority = _matching_priority(profile.metadata)
    model_requirements, model_requirements_valid = _effective_model_requirements(
        profile.model.requirements,
        profile.model.routing_profile_ref,
        routing_profiles or {},
    )
    return AgentMatchCandidate(
        ref=AgentRevisionRef(revision.agent_id, revision.revision),
        kind=AgentCandidateKind.AGENT,
        roles=(profile.role,),
        enabled=profile.enabled,
        owner_ref=revision.owner_ref,
        project_id=revision.project_id,
        workspace_id=revision.workspace_id,
        allowed_capability_ids=tuple(
            dict.fromkeys((*profile.capabilities.allowed, *profile.capabilities.required_ids))
        ),
        denied_capability_ids=profile.capabilities.denied,
        capability_constraints=profile.capabilities.constraints,
        capabilities_unrestricted=not profile.capabilities.allowed,
        policy_refs=tuple(dict.fromkeys(policies)),
        model_requirements=(model_requirements,),
        model_requirements_valid=model_requirements_valid,
        allows_task_model_override=profile.model.allow_task_override,
        matching_priority=priority,
    )


@dataclass(frozen=True, slots=True)
class _TeamPolicyProjection:
    allowed: tuple[str, ...]
    denied: tuple[str, ...]
    constraints: tuple[CapabilityConstraint, ...]
    policies: tuple[str, ...]
    model_requirements: tuple[RoutingRequirements, ...]
    model_requirements_valid: bool
    capabilities_unrestricted: bool
    allows_task_model_override: bool


def _team_candidate(
    revision: AgentTeamRevision,
    repository: AgentRepository,
    routing_profiles: Mapping[str, RoutingRequirements] | None = None,
) -> AgentMatchCandidate:
    member_pairs = _team_member_pairs(revision, repository)
    active_pairs, enabled = _active_team_members(revision, member_pairs)
    policy_members = _team_policy_members(revision, active_pairs)
    projection = _team_policy_projection(revision, policy_members, routing_profiles or {})
    if policy_members and not _members_allow_capabilities(
        policy_members,
        revision.profile.shared_capability_ids,
    ):
        enabled = False
    return AgentMatchCandidate(
        ref=AgentTeamRevisionRef(revision.team_id, revision.revision),
        kind=AgentCandidateKind.TEAM,
        roles=_team_roles(active_pairs),
        enabled=enabled,
        owner_ref=revision.owner_ref,
        project_id=revision.project_id,
        workspace_id=revision.workspace_id,
        allowed_capability_ids=projection.allowed,
        denied_capability_ids=projection.denied,
        capability_constraints=projection.constraints,
        capabilities_unrestricted=projection.capabilities_unrestricted,
        policy_refs=projection.policies,
        model_requirements=projection.model_requirements,
        model_requirements_valid=projection.model_requirements_valid,
        allows_task_model_override=projection.allows_task_model_override,
        member_refs=tuple(
            AgentRevisionRef(member.agent_id, member.revision) for _, member in active_pairs
        ),
        matching_priority=_matching_priority(revision.profile.metadata),
    )


def _team_member_pairs(
    revision: AgentTeamRevision,
    repository: AgentRepository,
) -> tuple[tuple[AgentTeamMember, AgentRevision], ...]:
    member_revisions = tuple(
        repository.get_agent_revision(member.agent.agent_id, member.agent.revision)
        for member in revision.profile.members
    )
    return tuple(zip(revision.profile.members, member_revisions, strict=True))


def _active_team_members(
    revision: AgentTeamRevision,
    member_pairs: tuple[tuple[AgentTeamMember, AgentRevision], ...],
) -> tuple[tuple[tuple[AgentTeamMember, AgentRevision], ...], bool]:
    enabled = revision.profile.enabled
    active_pairs: list[tuple[AgentTeamMember, AgentRevision]] = []
    for member, member_revision in member_pairs:
        if member_revision.profile.enabled:
            active_pairs.append((member, member_revision))
            continue
        if (
            member.required
            or revision.profile.unavailable_member_policy is UnavailableMemberPolicy.FAIL
        ):
            enabled = False
    if not active_pairs:
        enabled = False
    return tuple(active_pairs), enabled


def _team_policy_members(
    revision: AgentTeamRevision,
    active_pairs: tuple[tuple[AgentTeamMember, AgentRevision], ...],
) -> tuple[AgentRevision, ...]:
    if revision.profile.unavailable_member_policy is UnavailableMemberPolicy.FAIL:
        return tuple(member_revision for _, member_revision in active_pairs)
    return tuple(member_revision for member, member_revision in active_pairs if member.required)


def _team_roles(
    active_pairs: tuple[tuple[AgentTeamMember, AgentRevision], ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [member.role for member, _ in active_pairs]
            + [member_revision.profile.role for _, member_revision in active_pairs]
        )
    )


def _team_policy_projection(
    revision: AgentTeamRevision,
    policy_members: tuple[AgentRevision, ...],
    routing_profiles: Mapping[str, RoutingRequirements],
) -> _TeamPolicyProjection:
    restricted_allowlists = [
        set(member.profile.capabilities.allowed)
        for member in policy_members
        if member.profile.capabilities.allowed
    ]
    allowed = set.intersection(*restricted_allowlists) if restricted_allowlists else set()
    denied: set[str] = set()
    constraints: list[CapabilityConstraint] = []
    policies: set[str] = set()
    model_requirements: list[RoutingRequirements] = []
    model_requirements_valid = True
    if revision.profile.coordination_policy_ref is not None:
        policies.add(revision.profile.coordination_policy_ref)
    for member_revision in policy_members:
        denied.update(member_revision.profile.capabilities.denied)
        constraints.extend(member_revision.profile.capabilities.constraints)
        effective_model, valid_model = _effective_model_requirements(
            member_revision.profile.model.requirements,
            member_revision.profile.model.routing_profile_ref,
            routing_profiles,
        )
        model_requirements.append(effective_model)
        model_requirements_valid = model_requirements_valid and valid_model
        policies.update(member_revision.profile.policy_hooks.verification_policy_refs)
        if member_revision.profile.policy_hooks.authorization_profile_ref is not None:
            policies.add(member_revision.profile.policy_hooks.authorization_profile_ref)
    return _TeamPolicyProjection(
        allowed=tuple(sorted(allowed)),
        denied=tuple(sorted(denied)),
        constraints=tuple(constraints),
        policies=tuple(sorted(policies)),
        model_requirements=tuple(model_requirements),
        model_requirements_valid=model_requirements_valid,
        capabilities_unrestricted=bool(policy_members) and not restricted_allowlists,
        allows_task_model_override=(
            bool(policy_members)
            and all(member.profile.model.allow_task_override for member in policy_members)
        ),
    )


def _members_allow_capabilities(
    members: tuple[AgentRevision, ...],
    capability_ids: tuple[str, ...],
) -> bool:
    requested = set(capability_ids)
    for member in members:
        policy = member.profile.capabilities
        if requested.intersection(policy.denied):
            return False
        if policy.allowed and not requested.issubset(policy.allowed):
            return False
    return True


def _effective_model_requirements(
    inline: RoutingRequirements,
    routing_profile_ref: str | None,
    routing_profiles: Mapping[str, RoutingRequirements],
) -> tuple[RoutingRequirements, bool]:
    if routing_profile_ref is None:
        return inline, True
    profile_requirements = routing_profiles.get(routing_profile_ref)
    if profile_requirements is None:
        return inline, False
    merged = _merge_model_requirements(
        profile_requirements,
        inline,
        overlay_replaces_explicit=True,
    )
    if merged is None:
        return inline, False
    return merged, True


def _matching_priority(metadata: Mapping[str, JsonValue]) -> int:
    value = metadata.get("matching_priority")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


__all__ = ["AgentResolver"]
