from __future__ import annotations

from ai_multi_agent_platform.agents import (
    AgentCandidateKind,
    AgentInstructions,
    AgentMatchingRequirements,
    AgentMatchReason,
    AgentMatchStatus,
    AgentProfile,
    AgentResolver,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    AgentTeamRevisionRef,
    InMemoryAgentRepository,
    InstructionSource,
    UnavailableMemberPolicy,
)
from ai_multi_agent_platform.domain import OwnerRef

OWNER = OwnerRef(type="user", id="issue-903-team-owner")


def _create_agent(
    service: AgentService,
    *,
    name: str,
    role: str,
    enabled: bool = True,
) -> AgentRevisionRef:
    revision = service.create_agent(
        AgentProfile(
            name=name,
            role=role,
            enabled=enabled,
            instructions=AgentInstructions(role=InstructionSource(content=f"Act as {role}.")),
        ),
        owner_ref=OWNER,
    )
    return AgentRevisionRef(revision.agent_id, revision.revision)


def test_skip_optional_team_ignores_disabled_optional_member() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    researcher = _create_agent(service, name="Researcher", role="researcher")
    disabled_reviewer = _create_agent(
        service,
        name="Disabled reviewer",
        role="reviewer",
        enabled=False,
    )
    team = service.create_team(
        AgentTeamProfile(
            name="Research team",
            members=(
                AgentTeamMember(agent=researcher, role="researcher", required=True),
                AgentTeamMember(agent=disabled_reviewer, role="reviewer", required=False),
            ),
            unavailable_member_policy=UnavailableMemberPolicy.SKIP_OPTIONAL,
        ),
        owner_ref=OWNER,
    )

    resolver = AgentResolver(repository)
    result = resolver.resolve(
        AgentMatchingRequirements(
            required_roles=("researcher",),
            candidate_kinds=(AgentCandidateKind.TEAM,),
        )
    )

    assert result.status is AgentMatchStatus.SELECTED
    assert result.selected == AgentTeamRevisionRef(team.team_id, team.revision)
    assert result.selected_outcome is not None
    assert result.selected_outcome.candidate.member_refs == (researcher,)
    assert "reviewer" not in result.selected_outcome.candidate.roles

    reviewer_result = resolver.resolve(
        AgentMatchingRequirements(
            required_roles=("reviewer",),
            candidate_kinds=(AgentCandidateKind.TEAM,),
        )
    )
    assert reviewer_result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.ROLE_MISMATCH
        for outcome in reviewer_result.outcomes
        for rejection in outcome.rejections
    )


def test_fail_team_is_disabled_when_optional_member_is_unavailable() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    researcher = _create_agent(service, name="Researcher", role="researcher")
    disabled_reviewer = _create_agent(
        service,
        name="Disabled reviewer",
        role="reviewer",
        enabled=False,
    )
    service.create_team(
        AgentTeamProfile(
            name="Strict research team",
            members=(
                AgentTeamMember(agent=researcher, role="researcher", required=True),
                AgentTeamMember(agent=disabled_reviewer, role="reviewer", required=False),
            ),
            unavailable_member_policy=UnavailableMemberPolicy.FAIL,
        ),
        owner_ref=OWNER,
    )

    result = AgentResolver(repository).resolve(
        AgentMatchingRequirements(
            required_roles=("researcher",),
            candidate_kinds=(AgentCandidateKind.TEAM,),
        )
    )

    assert result.status is AgentMatchStatus.NO_MATCH
    assert any(
        rejection.reason is AgentMatchReason.DISABLED
        for outcome in result.outcomes
        for rejection in outcome.rejections
    )
