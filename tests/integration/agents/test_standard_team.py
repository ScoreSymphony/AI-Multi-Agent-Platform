from __future__ import annotations

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    STANDARD_TEAM_IDS,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
    clone_standard_team,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
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


def _request(verification: VerificationService):
    policy = verification.register_policy(
        VerificationPolicy(
            name="standard-team-review",
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
        digest="sha256:standard-team-result",
    )
    request = verification.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id="issue-711-standard-team",
    )
    return policy, request


def test_bundled_software_development_team_resolves_real_reviewer_tester_member() -> None:
    service = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(service)
    runtime = AgentRuntime(service)
    verification = VerificationService()
    policy, request = _request(verification)

    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                team_id=STANDARD_TEAM_IDS["software_development"],
                team_revision=1,
                team_role="reviewer_tester",
            )
        }
    )

    resolved = resolver.resolve(request, runtime)

    assert resolved.team_id == STANDARD_TEAM_IDS["software_development"]
    assert resolved.team_revision == 1
    assert resolved.agent_id == STANDARD_AGENT_IDS["reviewer"]
    assert resolved.agent_revision == 1


def test_cloned_software_development_team_uses_same_generic_reviewer_resolution() -> None:
    service = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(service)
    cloned = clone_standard_team(
        service,
        "software_development",
        owner_ref=OwnerRef(type="user", id="issue-711-owner"),
        name="Custom Development Review Team",
    )
    runtime = AgentRuntime(service)
    verification = VerificationService()
    policy, request = _request(verification)

    resolver = ConfiguredReviewerResolver(
        {
            (policy.policy_id, policy.version, "review"): ReviewerAssignment(
                team_id=cloned.team_id,
                team_revision=cloned.revision,
                team_role="reviewer_tester",
            )
        }
    )

    resolved = resolver.resolve(request, runtime)

    assert resolved.team_id == cloned.team_id
    assert resolved.team_revision == cloned.revision
    assert resolved.agent_id == STANDARD_AGENT_IDS["reviewer"]
    assert resolved.agent_revision == 1
