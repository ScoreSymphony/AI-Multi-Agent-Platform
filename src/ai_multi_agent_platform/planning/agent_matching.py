"""#439 adapter from planning inventory/Step requirements to canonical #903 matching."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents import (
    AgentCandidateKind,
    AgentCapabilityRequirement,
    AgentMatchCandidate,
    AgentMatcher,
    AgentMatchingRequirements,
    AgentMatchResult,
)
from ai_multi_agent_platform.agents.models import (
    AgentRevisionRef,
    AgentTeamRevisionRef,
    CapabilityConstraint,
)
from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    AgentAssignment,
    PlanningAgentCandidate,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTeamCandidate,
)

_PLANNING_OWNER = OwnerRef(type="service", id="planning-inventory")


def match_planning_step(
    step: PlanningStepDraft,
    request: PlanningRequest,
    *,
    agent_only: bool = False,
) -> AgentMatchResult | None:
    """Resolve one planner assignment against the already authorized planning inventory.

    The inventory is the #439/#15 server-resolved view. This adapter does not re-authorize or
    schedule workers; it only translates that trusted snapshot to the shared matcher contract.
    """

    assignment = step.assignment
    if assignment is None:
        return None
    required_capabilities = tuple(
        AgentCapabilityRequirement(
            requirement.capability_id,
            exact_version=requirement.exact_version,
            required_features=requirement.required_features,
        )
        for requirement in step.capability_requirements
        if requirement.required
    )
    kinds = (
        (AgentCandidateKind.AGENT,)
        if agent_only
        else (AgentCandidateKind.AGENT, AgentCandidateKind.TEAM)
    )
    matching = AgentMatchingRequirements(
        required_roles=(assignment.role_requirement,) if assignment.role_requirement else (),
        required_capabilities=required_capabilities,
        model_requirements=step.model_requirements,
        model_requirements_are_task_override=True,
        workspace_id=step.workspace_id or request.workspace_id,
        exact_agent=(
            AgentRevisionRef(assignment.agent_id, assignment.agent_revision)
            if assignment.agent_id is not None and assignment.agent_revision is not None
            else None
        ),
        exact_team=(
            AgentTeamRevisionRef(assignment.team_id, assignment.team_revision)
            if assignment.team_id is not None and assignment.team_revision is not None
            else None
        ),
        candidate_kinds=kinds,
    )
    matcher = AgentMatcher(capability_specs=_capability_specs(request))
    return matcher.match(matching, _planning_candidates(request, agent_only=agent_only))


def resolve_planning_steps(
    steps: tuple[PlanningStepDraft, ...],
    request: PlanningRequest,
) -> tuple[PlanningStepDraft, ...]:
    """Resolve role-only #439 assignments to exact Agent revisions when unambiguous.

    The current reference execution boundary executes one Agent per canonical Step, so this
    adapter intentionally asks the shared matcher for Agents only. Team matching remains
    available through the core resolver without pretending the single-Agent execution seam can
    execute a Team. Ambiguous/no-match assignments remain role-based and are rejected by the
    proposal validator with structured diagnostics.
    """

    resolved: list[PlanningStepDraft] = []
    for step in steps:
        assignment = step.assignment
        if assignment is None or assignment.role_requirement is None:
            resolved.append(step)
            continue
        result = match_planning_step(step, request, agent_only=True)
        if result is None or not isinstance(result.selected, AgentRevisionRef):
            resolved.append(step)
            continue
        rationale = assignment.rationale
        selected_outcome = result.selected_outcome
        matching_rationale = (
            "canonical #903 Agent matcher selected exact eligible revision"
            if selected_outcome is None
            else "; ".join(selected_outcome.rationale)
        )
        if rationale:
            matching_rationale = f"{rationale}; {matching_rationale}"
        resolved.append(
            replace(
                step,
                assignment=AgentAssignment(
                    agent_id=result.selected.agent_id,
                    agent_revision=result.selected.revision,
                    rationale=matching_rationale,
                ),
            )
        )
    return tuple(resolved)


def _capability_specs(request: PlanningRequest) -> tuple[CapabilitySpec, ...]:
    return tuple(
        CapabilitySpec(
            capability_id=item.capability_id,
            name=item.capability_id,
            version=item.version,
            available=item.available,
            health=HealthStatus.HEALTHY if item.available else HealthStatus.UNAVAILABLE,
            features=item.features,
        )
        for item in request.inventory.capabilities
    )


def _planning_candidates(
    request: PlanningRequest,
    *,
    agent_only: bool,
) -> tuple[AgentMatchCandidate, ...]:
    agents = tuple(_agent_candidate(item) for item in request.inventory.agents)
    if agent_only:
        return agents
    by_id = {item.agent_id: item for item in request.inventory.agents}
    teams = tuple(_team_candidate(item, by_id) for item in request.inventory.teams)
    return (*agents, *teams)


def _agent_candidate(candidate: PlanningAgentCandidate) -> AgentMatchCandidate:
    allowed = tuple(
        dict.fromkeys((*candidate.allowed_capability_ids, *candidate.required_capability_ids))
    )
    constraints = tuple(
        CapabilityConstraint(capability_id=capability_id, required=True)
        for capability_id in candidate.required_capability_ids
    )
    return AgentMatchCandidate(
        ref=AgentRevisionRef(candidate.agent_id, candidate.revision),
        kind=AgentCandidateKind.AGENT,
        roles=(candidate.role,),
        enabled=candidate.enabled,
        owner_ref=_PLANNING_OWNER,
        # PlanningInventoryBuilder already filters by the Task project. PlanningRequest
        # intentionally carries no project ID, so do not reject that trusted projection
        # merely because the shared matcher cannot reconstruct the Task project here.
        project_id=None,
        workspace_id=candidate.workspace_id,
        allowed_capability_ids=allowed,
        denied_capability_ids=candidate.denied_capability_ids,
        capability_constraints=constraints,
        capabilities_unrestricted=not candidate.allowed_capability_ids,
        model_requirements=(candidate.model_requirements,),
        allows_task_model_override=candidate.allow_task_model_override,
    )


def _team_candidate(
    candidate: PlanningTeamCandidate,
    agents: dict[str, PlanningAgentCandidate],
) -> AgentMatchCandidate:
    members = tuple(
        agents[agent_id] for agent_id in candidate.member_agent_ids if agent_id in agents
    )
    active_members = tuple(member for member in members if member.enabled)
    required_ids = set(candidate.required_member_agent_ids)
    if candidate.skip_optional_unavailable and required_ids:
        policy_members = tuple(
            member for member in active_members if member.agent_id in required_ids
        )
    else:
        # FAIL requires every active member to remain executable. For an all-optional
        # SKIP_OPTIONAL Team, use all active members conservatively rather than inventing an
        # existential aggregate that AgentMatchCandidate cannot represent safely.
        policy_members = active_members

    roles = tuple(dict.fromkeys(member.role for member in active_members))
    restricted_allowlists = [
        set(member.allowed_capability_ids)
        for member in policy_members
        if member.allowed_capability_ids
    ]
    allowed = set.intersection(*restricted_allowlists) if restricted_allowlists else set()
    denied: set[str] = set()
    constraints: list[CapabilityConstraint] = []
    member_refs = tuple(
        AgentRevisionRef(member.agent_id, member.revision) for member in active_members
    )
    enabled = candidate.enabled and bool(active_members)
    for member in policy_members:
        denied.update(member.denied_capability_ids)
        constraints.extend(
            CapabilityConstraint(capability_id=capability_id, required=True)
            for capability_id in member.required_capability_ids
        )

    shared = set(candidate.shared_capability_ids)
    for member in policy_members:
        if shared.intersection(member.denied_capability_ids):
            enabled = False
        if member.allowed_capability_ids and not shared.issubset(member.allowed_capability_ids):
            enabled = False

    return AgentMatchCandidate(
        ref=AgentTeamRevisionRef(candidate.team_id, candidate.revision),
        kind=AgentCandidateKind.TEAM,
        roles=roles,
        enabled=enabled,
        owner_ref=_PLANNING_OWNER,
        # Team scope was already filtered by PlanningInventoryBuilder as well.
        project_id=None,
        workspace_id=candidate.workspace_id,
        allowed_capability_ids=tuple(sorted(allowed)),
        denied_capability_ids=tuple(sorted(denied)),
        capability_constraints=tuple(constraints),
        capabilities_unrestricted=bool(policy_members) and not restricted_allowlists,
        model_requirements=tuple(member.model_requirements for member in policy_members),
        allows_task_model_override=(
            bool(policy_members)
            and all(member.allow_task_model_override for member in policy_members)
        ),
        member_refs=member_refs,
    )


__all__ = ["match_planning_step", "resolve_planning_steps"]
