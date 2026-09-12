from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentCandidateKind,
    AgentCapabilityPolicy,
    AgentCapabilityRequirement,
    AgentInstructions,
    AgentMatchCandidate,
    AgentMatchReason,
    AgentMatchResult,
    AgentMatchStatus,
    AgentMatcher,
    AgentMatchingRequirements,
    AgentProfile,
    AgentResolver,
    AgentRevisionRef,
    AgentService,
    AgentTieBreakPolicy,
    CallableAgentMatchingPolicy,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.handoffs import CanonicalConsumerRequirementEvaluator
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.planning.agent_matching import resolve_planning_steps
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    CapabilityRequirement,
    PlanDraft,
    PlannerDescriptor,
    PlannerKind,
    PlannerOutput,
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningRequest,
    PlanningStepDraft,
)
from ai_multi_agent_platform.planning.proposals import PlanningProposalFactory
from ai_multi_agent_platform.testing import FakeModelProvider

OWNER = OwnerRef(type="user", id="issue-903-owner")


def _candidate(
    *,
    role: str = "researcher",
    capabilities: tuple[str, ...] = ("tool.search",),
    denied: tuple[str, ...] = (),
    constraints: tuple[CapabilityConstraint, ...] = (),
    enabled: bool = True,
    project_id: str | None = None,
    model_requirements: RoutingRequirements | None = None,
    priority: int = 0,
) -> AgentMatchCandidate:
    return AgentMatchCandidate(
        ref=AgentRevisionRef(new_id("agent"), 1),
        kind=AgentCandidateKind.AGENT,
        roles=(role,),
        enabled=enabled,
        owner_ref=OWNER,
        project_id=project_id,
        allowed_capability_ids=capabilities,
        denied_capability_ids=denied,
        capability_constraints=constraints,
        model_requirements=(model_requirements or RoutingRequirements(),),
        matching_priority=priority,
    )


def _search_capability(version: str = "1.5") -> CapabilitySpec:
    return CapabilitySpec(
        capability_id="tool.search",
        name="Search",
        version=version,
        available=True,
        health=HealthStatus.HEALTHY,
        features=("citations",),
    )


def _reasons(result: AgentMatchResult) -> set[AgentMatchReason]:
    return {
        rejection.reason
        for outcome in result.outcomes
        for rejection in outcome.rejections
    }


def test_exact_role_and_capability_match() -> None:
    candidate = _candidate()
    matcher = AgentMatcher(capability_specs=(_search_capability(),))

    result = matcher.match(
        AgentMatchingRequirements(
            required_roles=("researcher",),
            required_capabilities=(AgentCapabilityRequirement("tool.search"),),
        ),
        (candidate,),
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected == candidate.ref


def test_missing_required_capability_and_denied_capability_are_explainable() -> None:
    missing = _candidate(capabilities=("tool.other",))
    denied = _candidate(capabilities=("tool.search",), denied=("tool.search",))
    matcher = AgentMatcher(capability_specs=(_search_capability(),))
    requirements = AgentMatchingRequirements(
        required_roles=("researcher",),
        required_capabilities=(AgentCapabilityRequirement("tool.search"),),
    )

    missing_result = matcher.match(requirements, (missing,))
    denied_result = matcher.match(requirements, (denied,))

    assert missing_result.status is AgentMatchStatus.NO_MATCH
    assert AgentMatchReason.MISSING_CAPABILITY in _reasons(missing_result)
    assert denied_result.status is AgentMatchStatus.NO_MATCH
    assert AgentMatchReason.DENIED_CAPABILITY in _reasons(denied_result)


def test_capability_version_range_uses_canonical_inventory_versions() -> None:
    candidate = _candidate(
        constraints=(
            CapabilityConstraint(
                capability_id="tool.search",
                required=True,
                minimum_version="1.2",
                maximum_version="2.0",
            ),
        )
    )
    matcher = AgentMatcher(capability_specs=(_search_capability("1.5"),))

    result = matcher.match(
        AgentMatchingRequirements(
            required_capabilities=(
                AgentCapabilityRequirement(
                    "tool.search",
                    minimum_version="1.4",
                    maximum_version="1.8",
                    include_maximum=True,
                    required_features=("citations",),
                ),
            ),
        ),
        (candidate,),
    )

    assert result.status is AgentMatchStatus.SELECTED


def test_disabled_scope_and_policy_denials_are_filtered() -> None:
    project_id = new_id("project")
    disabled = _candidate(enabled=False)
    wrong_scope = _candidate(project_id=project_id)
    denied_by_policy = _candidate()
    matcher = AgentMatcher(
        capability_specs=(_search_capability(),),
        policy=CallableAgentMatchingPolicy(
            lambda candidate, requirements: candidate is not denied_by_policy
        ),
    )
    requirements = AgentMatchingRequirements(
        required_capabilities=(AgentCapabilityRequirement("tool.search"),),
    )

    result = matcher.match(requirements, (disabled, wrong_scope, denied_by_policy))

    assert result.status is AgentMatchStatus.NO_MATCH
    reasons = _reasons(result)
    assert AgentMatchReason.DISABLED in reasons
    assert AgentMatchReason.WRONG_SCOPE in reasons
    assert AgentMatchReason.POLICY_DENIED in reasons


def test_local_only_model_requirement_filters_remote_only_candidate() -> None:
    models = ModelRegistry()
    models.register_provider(FakeModelProvider())
    models.register_model(
        ModelConfiguration(
            config_id="model-remote-only",
            display_name="Remote only",
            provider_id="fake-model",
            location=ModelLocation.REMOTE,
            capabilities=ModelCapabilities(context_window=32_000),
        )
    )
    candidate = _candidate()
    matcher = AgentMatcher(model_registry=models)

    result = matcher.match(
        AgentMatchingRequirements(model_requirements=RoutingRequirements(local_only=True)),
        (candidate,),
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert AgentMatchReason.MODEL_REQUIREMENT_INCOMPATIBLE in _reasons(result)


def test_reviewer_independence_excludes_producer_revision() -> None:
    producer = _candidate(role="reviewer")
    reviewer = _candidate(role="reviewer")
    matcher = AgentMatcher()

    result = matcher.match(
        AgentMatchingRequirements(
            required_roles=("reviewer",),
            excluded_participants=(producer.ref,),
        ),
        (producer, reviewer),
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected == reviewer.ref
    producer_outcome = next(item for item in result.outcomes if item.candidate.ref == producer.ref)
    assert producer_outcome.rejections[0].reason is AgentMatchReason.INDEPENDENCE_VIOLATION


def test_equal_candidates_are_ambiguous_unless_explicit_tie_break_is_configured() -> None:
    first = _candidate()
    second = _candidate()
    matcher = AgentMatcher()

    ambiguous = matcher.match(AgentMatchingRequirements(), (second, first))
    deterministic = matcher.match(
        AgentMatchingRequirements(tie_break=AgentTieBreakPolicy.CANONICAL_ID),
        (second, first),
    )

    assert ambiguous.status is AgentMatchStatus.AMBIGUOUS
    assert ambiguous.selected is None
    assert deterministic.status is AgentMatchStatus.SELECTED
    assert deterministic.selected == min(
        (first.ref, second.ref),
        key=lambda ref: (ref.agent_id, ref.revision),
    )


def test_custom_agent_preference_is_data_driven_not_bundled_name_driven() -> None:
    bundled = _candidate(priority=0)
    custom = _candidate(priority=10)
    matcher = AgentMatcher()

    result = matcher.match(AgentMatchingRequirements(), (bundled, custom))

    assert result.selected == custom.ref


def test_exact_pin_skips_ambiguity_and_only_evaluates_requested_revision() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    first = service.create_agent(
        AgentProfile(
            name="First",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
        ),
        owner_ref=OWNER,
    )
    second = service.create_agent(
        AgentProfile(
            name="Second",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
        ),
        owner_ref=OWNER,
    )
    pinned = AgentRevisionRef(second.agent_id, second.revision)

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(exact_agent=pinned, required_roles=("researcher",))
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected == pinned
    assert len(result.outcomes) == 1
    assert result.outcomes[0].candidate.ref != AgentRevisionRef(first.agent_id, first.revision)


def test_handoff_consumer_requirement_evaluator_uses_shared_exact_matcher() -> None:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Late-bound researcher",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
            capabilities=AgentCapabilityPolicy(allowed=("tool.search",)),
        ),
        owner_ref=OWNER,
    )
    consumer = AgentRevisionRef(revision.agent_id, revision.revision)
    evaluator = CanonicalConsumerRequirementEvaluator(repository)

    assert evaluator.accepts(("role:researcher", "capability:tool.search"), consumer)
    assert not evaluator.accepts(("role:reviewer",), consumer)


def test_planning_role_assignment_resolves_to_exact_agent_revision() -> None:
    agent_id = new_id("agent")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Research the topic",
        context=OperationContext(correlation_id="issue-903-planning"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=agent_id,
                    revision=3,
                    role="researcher",
                    allowed_capability_ids=("tool.search",),
                ),
            ),
            capabilities=(
                PlanningCapabilityCandidate(
                    capability_id="tool.search",
                    version="1.0",
                    available=True,
                ),
            ),
        ),
    )
    step = PlanningStepDraft(
        key="research",
        title="Research",
        assignment=AgentAssignment(role_requirement="researcher"),
        capability_requirements=(CapabilityRequirement("tool.search"),),
    )

    resolved = resolve_planning_steps((step,), request)
    proposal = PlanningProposalFactory().build(
        request,
        PlannerOutput(
            draft=PlanDraft(summary="Research plan", steps=(step,)),
            planner=PlannerDescriptor("issue-903-planner", PlannerKind.DETERMINISTIC),
        ),
    )

    assert resolved[0].assignment is not None
    assert resolved[0].assignment.agent_id == agent_id
    assert resolved[0].assignment.agent_revision == 3
    assert proposal.steps[0].assignment is not None
    assert proposal.steps[0].assignment.agent_id == agent_id


def test_model_provider_replacement_preserves_canonical_agent_identity() -> None:
    models = ModelRegistry()
    models.register_provider(FakeModelProvider())
    models.register_model(
        ModelConfiguration(
            config_id="model-local-stable",
            display_name="Stable local model",
            provider_id="fake-model",
            location=ModelLocation.LOCAL,
            capabilities=ModelCapabilities(context_window=8_000),
        )
    )
    candidate = _candidate()
    matcher = AgentMatcher(model_registry=models)
    requirements = AgentMatchingRequirements(
        model_requirements=RoutingRequirements(local_only=True)
    )

    before = matcher.match(requirements, (candidate,))
    models.replace_provider(FakeModelProvider())
    after = matcher.match(requirements, (candidate,))

    assert before.selected == candidate.ref
    assert after.selected == candidate.ref
