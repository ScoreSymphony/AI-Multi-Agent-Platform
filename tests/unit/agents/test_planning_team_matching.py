from __future__ import annotations

from ai_multi_agent_platform.agents import AgentMatchReason, AgentMatchStatus
from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.planning.agent_matching import match_planning_step
from ai_multi_agent_platform.planning.models import (
    AgentAssignment,
    CapabilityRequirement,
    PlanningAgentCandidate,
    PlanningCapabilityCandidate,
    PlanningInventory,
    PlanningRequest,
    PlanningStepDraft,
    PlanningTeamCandidate,
)


def test_planning_team_does_not_union_optional_member_capability_into_required_member() -> None:
    required_agent_id = new_id("agent")
    optional_agent_id = new_id("agent")
    team_id = new_id("team")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Search with a Team",
        context=OperationContext(correlation_id="issue-903-planning-team"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=required_agent_id,
                    revision=1,
                    role="researcher",
                    allowed_capability_ids=("tool.review",),
                ),
                PlanningAgentCandidate(
                    agent_id=optional_agent_id,
                    revision=1,
                    role="helper",
                    allowed_capability_ids=("tool.search",),
                ),
            ),
            teams=(
                PlanningTeamCandidate(
                    team_id=team_id,
                    revision=1,
                    enabled=True,
                    member_agent_ids=(required_agent_id, optional_agent_id),
                    required_member_agent_ids=(required_agent_id,),
                    skip_optional_unavailable=True,
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
        key="search",
        title="Search",
        assignment=AgentAssignment(team_id=team_id, team_revision=1),
        capability_requirements=(CapabilityRequirement("tool.search"),),
    )

    result = match_planning_step(step, request)

    assert result is not None
    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.MISSING_CAPABILITY
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )


def test_planning_team_skips_disabled_optional_member_from_roles_and_membership() -> None:
    required_agent_id = new_id("agent")
    optional_agent_id = new_id("agent")
    team_id = new_id("team")
    request = PlanningRequest(
        task_id=new_id("task"),
        task_revision=1,
        objective="Use available Team members",
        context=OperationContext(correlation_id="issue-903-planning-team-disabled"),
        inventory=PlanningInventory(
            agents=(
                PlanningAgentCandidate(
                    agent_id=required_agent_id,
                    revision=1,
                    role="researcher",
                ),
                PlanningAgentCandidate(
                    agent_id=optional_agent_id,
                    revision=1,
                    role="reviewer",
                    enabled=False,
                ),
            ),
            teams=(
                PlanningTeamCandidate(
                    team_id=team_id,
                    revision=1,
                    enabled=True,
                    member_agent_ids=(required_agent_id, optional_agent_id),
                    required_member_agent_ids=(required_agent_id,),
                    skip_optional_unavailable=True,
                ),
            ),
        ),
    )
    step = PlanningStepDraft(
        key="team",
        title="Team",
        assignment=AgentAssignment(team_id=team_id, team_revision=1),
    )

    result = match_planning_step(step, request)

    assert result is not None
    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected_outcome is not None
    assert result.selected_outcome.candidate.roles == ("researcher",)
    assert tuple(ref.agent_id for ref in result.selected_outcome.candidate.member_refs) == (
        required_agent_id,
    )
