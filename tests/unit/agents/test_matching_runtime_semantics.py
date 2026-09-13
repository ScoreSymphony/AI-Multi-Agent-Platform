from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentCandidateKind,
    AgentCapabilityPolicy,
    AgentCapabilityRequirement,
    AgentInstructions,
    AgentMatchingRequirements,
    AgentMatchReason,
    AgentMatchStatus,
    AgentModelPolicy,
    AgentProfile,
    AgentResolver,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelLocation, RoutingRequirements
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    PlannerDescriptor,
    PlannerKind,
    PlanningAgentCandidate,
    PlanningInventory,
    PlanningModelCandidate,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTrigger,
    PlanProposal,
)
from ai_multi_agent_platform.planning.validation import PlanningProposalValidator

OWNER = OwnerRef(type="user", id="issue-903-runtime-semantics-owner")


def _agent(
    service: AgentService,
    *,
    name: str,
    role: str,
    capabilities: AgentCapabilityPolicy | None = None,
    model: AgentModelPolicy | None = None,
) -> AgentRevisionRef:
    revision = service.create_agent(
        AgentProfile(
            name=name,
            role=role,
            instructions=AgentInstructions(role=InstructionSource(content=f"Act as {role}.")),
            capabilities=capabilities or AgentCapabilityPolicy(),
            model=model or AgentModelPolicy(),
        ),
        owner_ref=OWNER,
    )
    return AgentRevisionRef(revision.agent_id, revision.revision)


def test_required_team_members_intersect_nonempty_capability_allowlists() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    researcher = _agent(
        service,
        name="Researcher",
        role="researcher",
        capabilities=AgentCapabilityPolicy(allowed=("tool.search",)),
    )
    reviewer = _agent(
        service,
        name="Reviewer",
        role="reviewer",
        capabilities=AgentCapabilityPolicy(allowed=("tool.review",)),
    )
    service.create_team(
        AgentTeamProfile(
            name="Strict team",
            members=(
                AgentTeamMember(agent=researcher, role="researcher", required=True),
                AgentTeamMember(agent=reviewer, role="reviewer", required=True),
            ),
        ),
        owner_ref=OWNER,
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            required_capabilities=(AgentCapabilityRequirement("tool.search"),),
            candidate_kinds=(AgentCandidateKind.TEAM,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MISSING_CAPABILITY
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_agent_capability_maximum_version_is_exclusive_like_runtime() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = _agent(
        service,
        name="Bounded agent",
        role="researcher",
        capabilities=AgentCapabilityPolicy(
            allowed=("tool.search",),
            constraints=(
                CapabilityConstraint(
                    capability_id="tool.search",
                    maximum_version="2.0",
                ),
            ),
        ),
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            exact_agent=revision,
            required_capabilities=(AgentCapabilityRequirement("tool.search", exact_version="2.0"),),
            candidate_kinds=(AgentCandidateKind.AGENT,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.CAPABILITY_VERSION_INCOMPATIBLE
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_task_model_override_replaces_agent_explicit_model_when_allowed() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = _agent(
        service,
        name="Override agent",
        role="researcher",
        model=AgentModelPolicy(
            requirements=RoutingRequirements(explicit_model_id="model-a"),
            allow_task_override=True,
        ),
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            exact_agent=revision,
            model_requirements=RoutingRequirements(explicit_model_id="model-b"),
            model_requirements_are_task_override=True,
            candidate_kinds=(AgentCandidateKind.AGENT,),
        )
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected == revision


def test_conflicting_local_and_self_hosted_requirements_are_rejected() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = _agent(
        service,
        name="Self-hosted agent",
        role="researcher",
        model=AgentModelPolicy(
            requirements=RoutingRequirements(self_hosted_only=True),
            allow_task_override=True,
        ),
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            exact_agent=revision,
            model_requirements=RoutingRequirements(local_only=True),
            model_requirements_are_task_override=True,
            candidate_kinds=(AgentCandidateKind.AGENT,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_resolver_includes_referenced_routing_profile_requirements() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = _agent(
        service,
        name="Profiled agent",
        role="researcher",
        model=AgentModelPolicy(routing_profile_ref="local-profile"),
    )

    result = AgentResolver(
        repository,
        routing_profiles={"local-profile": RoutingRequirements(local_only=True)},
    ).resolve(
        AgentMatchingRequirements(
            exact_agent=revision,
            model_requirements=RoutingRequirements(self_hosted_only=True),
            candidate_kinds=(AgentCandidateKind.AGENT,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_missing_routing_profile_fails_closed() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    revision = _agent(
        service,
        name="Missing-profile agent",
        role="researcher",
        model=AgentModelPolicy(routing_profile_ref="missing-profile"),
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            exact_agent=revision,
            candidate_kinds=(AgentCandidateKind.AGENT,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_exact_planning_assignment_respects_model_override_policy() -> None:
    agent_id = new_id("agent")
    task_id = new_id("task")
    step = PlanningStepDraft(
        key="research",
        title="Research",
        assignment=AgentAssignment(agent_id=agent_id, agent_revision=1),
        model_requirements=RoutingRequirements(local_only=True),
    )
    request = PlanningRequest(
        task_id=task_id,
        task_revision=1,
        objective="Research locally",
        context=OperationContext(correlation_id="issue-903-exact-planning"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=agent_id,
                    revision=1,
                    role="researcher",
                    allow_task_model_override=False,
                ),
            ),
            models=(
                PlanningModelCandidate(
                    model_config_id="local-model",
                    enabled=True,
                    available=True,
                    location=ModelLocation.LOCAL,
                ),
            ),
        ),
    )
    proposal = PlanProposal(
        proposal_id=new_id("plan_proposal"),
        task_id=task_id,
        task_revision=1,
        plan_revision=1,
        trigger=PlanningTrigger.INITIAL,
        summary="Research",
        steps=(step,),
        planner=PlannerDescriptor(planner_id="issue-903-test", kind=PlannerKind.DETERMINISTIC),
    )

    validation = PlanningProposalValidator().validate(proposal, request)

    assert not validation.valid
    assert any("exact Agent assignment is not eligible" in error for error in validation.errors)
