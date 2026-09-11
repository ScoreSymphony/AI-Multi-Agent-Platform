from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    STANDARD_TEAM_IDS,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerificationSubject,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import (
    ConfiguredReviewerResolver,
    ReviewerAssignment,
)
from ai_multi_agent_platform.verification.reviewer_routing import (
    CapabilityRoleReviewerResolver,
    ReviewerDiscoverySelector,
)


def _stack():
    service = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(service)
    runtime = AgentRuntime(service)
    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="disabled-reviewer-conformance",
            stages=(
                VerificationStage(
                    stage_id="review",
                    verifier_kind=VerifierKind.AGENT,
                    accepted_outcomes=(VerificationOutcome.PASS,),
                ),
            ),
        )
    )
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="sha256:issue-759-disabled-routing",
    )
    request = verification.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id="issue-759-disabled-routing",
    )
    return service, runtime, policy, request


def test_exact_standalone_disabled_reviewer_fails_during_resolution() -> None:
    service, runtime, policy, request = _stack()
    reviewer_id = STANDARD_AGENT_IDS["reviewer"]
    current = service.get_agent_revision(reviewer_id)
    disabled = service.update_agent(reviewer_id, replace(current.profile, enabled=False))
    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                agent_id=reviewer_id,
                agent_revision=disabled.revision,
            )
        }
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.UNAVAILABLE
    assert caught.value.message == f"reviewer Agent is disabled: {reviewer_id}@{disabled.revision}"


def test_exact_team_member_disabled_reviewer_fails_during_resolution() -> None:
    service, runtime, policy, request = _stack()
    reviewer_id = STANDARD_AGENT_IDS["reviewer"]
    reviewer = service.get_agent_revision(reviewer_id)
    disabled = service.update_agent(reviewer_id, replace(reviewer.profile, enabled=False))

    team = service.get_team_revision(STANDARD_TEAM_IDS["software_development"], 1)
    members = tuple(
        replace(
            member,
            agent=AgentRevisionRef(reviewer_id, disabled.revision),
        )
        if member.agent.agent_id == reviewer_id
        else member
        for member in team.profile.members
    )
    updated_team = service.update_team(
        team.team_id,
        replace(team.profile, members=members),
    )
    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                team_id=updated_team.team_id,
                team_revision=updated_team.revision,
                agent_id=reviewer_id,
                agent_revision=disabled.revision,
            )
        }
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.UNAVAILABLE
    assert caught.value.message == f"reviewer Agent is disabled: {reviewer_id}@{disabled.revision}"


def test_scoped_disabled_team_is_not_selected() -> None:
    service, runtime, policy, request = _stack()
    team_id = STANDARD_TEAM_IDS["software_development"]
    team = service.get_team_revision(team_id, 1)
    service.update_team(team_id, replace(team.profile, enabled=False))
    resolver = CapabilityRoleReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerDiscoverySelector(
                candidate_team_ids=(team_id,),
                reviewer_role="reviewer_tester",
            )
        }
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details["match_count"] == 0
