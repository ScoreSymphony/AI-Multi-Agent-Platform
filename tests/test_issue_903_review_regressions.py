from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentCapabilityRequirement,
    AgentInstructions,
    AgentMatcher,
    AgentMatchingRequirements,
    AgentMatchReason,
    AgentMatchStatus,
    AgentProfile,
    AgentResolver,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import CapabilitySpec
from ai_multi_agent_platform.contracts import HealthStatus, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.planning.agent_matching import match_planning_step, resolve_planning_steps
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    PlanningAgentCandidate,
    PlanningInventory,
    PlanningRequest,
    PlanningStepDraft,
)

OWNER = OwnerRef(type="user", id="issue-903-review-owner")


def test_default_empty_allowlist_remains_unrestricted_for_matching() -> None:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Default researcher",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
        ),
        owner_ref=OWNER,
    )
    matcher = AgentMatcher(
        capability_specs=(
            CapabilitySpec(
                capability_id="tool.search",
                name="Search",
                version="1.0",
                available=True,
                health=HealthStatus.HEALTHY,
            ),
        )
    )

    result = AgentResolver(repository, matcher=matcher).resolve(
        AgentMatchingRequirements(
            required_roles=("researcher",),
            required_capabilities=(AgentCapabilityRequirement("tool.search"),),
        )
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected is not None
    assert result.selected.agent_id == revision.agent_id


def test_project_scoped_agent_survives_trusted_planning_inventory_projection() -> None:
    agent_id = new_id("agent")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Research",
        context=OperationContext(correlation_id="issue-903-project-scope"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=agent_id,
                    revision=2,
                    role="researcher",
                    project_id=new_id("project"),
                ),
            )
        ),
    )
    step = PlanningStepDraft(
        key="research",
        title="Research",
        assignment=AgentAssignment(role_requirement="researcher"),
    )

    resolved = resolve_planning_steps((step,), request)

    assert resolved[0].assignment is not None
    assert resolved[0].assignment.agent_id == agent_id
    assert resolved[0].assignment.agent_revision == 2


def test_planning_rejects_task_model_override_for_agent_that_forbids_it() -> None:
    agent_id = new_id("agent")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Research locally",
        context=OperationContext(correlation_id="issue-903-model-override"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=agent_id,
                    revision=1,
                    role="researcher",
                    allow_task_model_override=False,
                ),
            )
        ),
    )
    step = PlanningStepDraft(
        key="research",
        title="Research",
        assignment=AgentAssignment(role_requirement="researcher"),
        model_requirements=RoutingRequirements(local_only=True),
    )

    result = match_planning_step(step, request, agent_only=True)
    resolved = resolve_planning_steps((step,), request)

    assert result is not None
    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MODEL_OVERRIDE_FORBIDDEN
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )
    assert resolved[0].assignment is not None
    assert resolved[0].assignment.agent_id is None
    assert resolved[0].assignment.role_requirement == "researcher"


def test_planning_accepts_task_model_override_when_agent_allows_it() -> None:
    agent_id = new_id("agent")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Research locally",
        context=OperationContext(correlation_id="issue-903-model-override-allowed"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=agent_id,
                    revision=4,
                    role="researcher",
                    allow_task_model_override=True,
                ),
            )
        ),
    )
    step = PlanningStepDraft(
        key="research",
        title="Research",
        assignment=AgentAssignment(role_requirement="researcher"),
        model_requirements=RoutingRequirements(local_only=True),
    )

    resolved = resolve_planning_steps((step,), request)

    assert resolved[0].assignment is not None
    assert resolved[0].assignment.agent_id == agent_id
    assert resolved[0].assignment.agent_revision == 4
