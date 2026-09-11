from __future__ import annotations

from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    STANDARD_TEAM_IDS,
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
            name="scoped-reviewer-discovery",
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
        digest="sha256:issue-759-routing",
    )
    request = verification.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        result_id=subject.subject_id,
        correlation_id="issue-759-routing",
    )
    return service, runtime, policy, request


def _resolver(
    policy: VerificationPolicy,
    selector: ReviewerDiscoverySelector,
) -> CapabilityRoleReviewerResolver:
    return CapabilityRoleReviewerResolver({(policy.policy_id, policy.version, "review"): selector})


def test_scoped_role_resolution_pins_current_agent_revision() -> None:
    service, runtime, policy, request = _stack()
    reviewer_id = STANDARD_AGENT_IDS["reviewer"]
    current = service.get_agent_revision(reviewer_id)
    updated = service.update_agent(
        reviewer_id,
        replace(current.profile, description="Custom current reviewer revision"),
    )

    resolved = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_agent_ids=(STANDARD_AGENT_IDS["developer"], reviewer_id),
            reviewer_role="reviewer",
        ),
    ).resolve(request, runtime)

    assert resolved.agent_id == reviewer_id
    assert resolved.agent_revision == updated.revision == 2
    assert resolved.team_id is None


def test_scoped_team_role_pins_team_and_member_revision() -> None:
    _, runtime, policy, request = _stack()

    resolved = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_team_ids=(STANDARD_TEAM_IDS["software_development"],),
            reviewer_role="reviewer_tester",
        ),
    ).resolve(request, runtime)

    assert resolved.team_id == STANDARD_TEAM_IDS["software_development"]
    assert resolved.team_revision == 1
    assert resolved.agent_id == STANDARD_AGENT_IDS["reviewer"]
    assert resolved.agent_revision == 1


def test_capability_resolution_stays_within_candidate_agents() -> None:
    _, runtime, policy, request = _stack()

    resolved = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_agent_ids=(
                STANDARD_AGENT_IDS["planner"],
                STANDARD_AGENT_IDS["reviewer"],
            ),
            required_capability_ids=("tool.file.read",),
        ),
    ).resolve(request, runtime)

    assert resolved.agent_id == STANDARD_AGENT_IDS["reviewer"]


def test_scoped_resolution_fails_closed_when_no_candidate_matches() -> None:
    _, runtime, policy, request = _stack()

    resolver = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_agent_ids=(STANDARD_AGENT_IDS["developer"],),
            reviewer_role="reviewer",
        ),
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details["match_count"] == 0


def test_scoped_resolution_fails_closed_when_multiple_routes_match() -> None:
    _, runtime, policy, request = _stack()

    resolver = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_team_ids=(
                STANDARD_TEAM_IDS["software_development"],
                STANDARD_TEAM_IDS["research"],
            ),
            reviewer_role="reviewer",
        ),
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details["match_count"] == 2


def test_disabled_scoped_agent_is_never_selected() -> None:
    service, runtime, policy, request = _stack()
    reviewer_id = STANDARD_AGENT_IDS["reviewer"]
    current = service.get_agent_revision(reviewer_id)
    service.update_agent(reviewer_id, replace(current.profile, enabled=False))

    resolver = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_agent_ids=(reviewer_id,),
            reviewer_role="reviewer",
        ),
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details["match_count"] == 0


def test_missing_candidate_is_not_replaced_by_bundled_reviewer() -> None:
    _, runtime, policy, request = _stack()

    resolver = _resolver(
        policy,
        ReviewerDiscoverySelector(
            candidate_agent_ids=(new_id("agent"),),
            reviewer_role="reviewer",
        ),
    )

    with pytest.raises(ContractError) as caught:
        resolver.resolve(request, runtime)

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert caught.value.details["match_count"] == 0


def test_selector_rejects_unbounded_global_discovery() -> None:
    with pytest.raises(ValueError, match="explicit Agent/Team candidate scope"):
        ReviewerDiscoverySelector(reviewer_role="reviewer")
