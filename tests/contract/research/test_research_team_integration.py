from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
    get_standard_agent_template,
    get_standard_team_template,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
    ResearchSourceType,
    ResearchVerificationBridge,
    ResearchVerificationSubjectType,
)
from ai_multi_agent_platform.research.models import EvidenceRelation
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerifierIdentity,
    VerifierKind,
)

_RESEARCHER_ID = STANDARD_AGENT_IDS["researcher"]
_REVIEWER_ID = STANDARD_AGENT_IDS["reviewer"]


def test_standard_research_team_preserves_member_role_and_capability_boundaries() -> None:
    agents = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(agents)
    team_template = get_standard_team_template("research")
    team = agents.get_team_revision(team_template.team_id)

    assert team.profile.leader_agent_id == _RESEARCHER_ID
    assert {
        (member.agent.agent_id, member.role)
        for member in team.profile.members
    } == {
        (_RESEARCHER_ID, "researcher"),
        (_REVIEWER_ID, "source_checker_reviewer"),
        (STANDARD_AGENT_IDS["data_analyst"], "analyst_writer"),
    }

    for key in ("researcher", "reviewer", "data_analyst"):
        policy = get_standard_agent_template(key).profile.capabilities
        assert "tool.file.write" in policy.denied
        assert "tool.shell.execute" in policy.denied

    runtime = AgentRuntime(agents)
    with pytest.raises(ContractError) as denied:
        asyncio.run(
            runtime.start_team(
                task_id=new_id("task"),
                run_id=new_id("run"),
                team_id=team.team_id,
                requested_capability_ids=("tool.file.write",),
                available_capability_ids=frozenset({"tool.file.write"}),
            )
        )
    assert denied.value.code is ErrorCode.FORBIDDEN


def test_standard_researcher_and_reviewer_use_canonical_research_and_verification() -> None:
    async def scenario() -> None:
        task_id = new_id("task")
        research = ResearchService(InMemoryResearchRepository())
        item = await research.create_item(
            title="Standard Research Team integration",
            question="Can existing standard roles use canonical Research without private runtime?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OwnerRef(type="user", id="research-team-owner"),
            task_id=task_id,
        )
        source = await research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.DOCUMENT,
            locator="artifact://research-team/source",
            title="Research Team fixture source",
        )
        observation = await research.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 7, 0, tzinfo=UTC),
            content_digest="sha256:research-team-source",
            identity_proven=True,
        )
        claim = await research.add_claim(
            item.research_item_id,
            text="The standard Researcher can produce source-bound canonical Claims.",
            category="team-integration",
            agent_id=_RESEARCHER_ID,
            agent_revision=1,
        )
        evidence = await research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
            agent_id=_RESEARCHER_ID,
            agent_revision=1,
        )
        supported = research.assess_claim(claim.claim_id)
        assert supported.status.value == "supported"

        verification = VerificationService(
            require_canonical_subjects=True,
            require_canonical_results=True,
        )
        policy = verification.register_policy(
            VerificationPolicy(
                name="standard Research Team independent review",
                stages=(VerificationStage("review", VerifierKind.AGENT),),
                independence=ReviewerIndependence(
                    producer_agent_must_differ=True,
                    agent_reviewer_must_be_read_only=True,
                    forbid_self_verification=True,
                ),
            )
        )
        bridge = ResearchVerificationBridge(research, verification)
        request = bridge.request_verification(
            subject_type=ResearchVerificationSubjectType.CLAIM,
            subject_id=claim.claim_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            correlation_id="research-team-review",
        )

        with pytest.raises(ContractError) as self_review:
            bridge.submit_result(
                VerificationResult(
                    verification_id=request.verification_id,
                    verifier=VerifierIdentity(
                        verifier_ref=f"agent:{_RESEARCHER_ID}",
                        kind=VerifierKind.AGENT,
                        agent_id=_RESEARCHER_ID,
                        agent_revision=1,
                        read_only=True,
                    ),
                    outcome=VerificationOutcome.PASS,
                    subject=request.subject,
                )
            )
        assert self_review.value.code is ErrorCode.FORBIDDEN

        accepted = bridge.submit_result(
            VerificationResult(
                verification_id=request.verification_id,
                verifier=VerifierIdentity(
                    verifier_ref=f"agent:{_REVIEWER_ID}",
                    kind=VerifierKind.AGENT,
                    agent_id=_REVIEWER_ID,
                    agent_revision=1,
                    read_only=True,
                ),
                outcome=VerificationOutcome.PASS,
                subject=request.subject,
                checks_executed=("source_check", "claim_support"),
            )
        )
        assert accepted.verifier.agent_id == _REVIEWER_ID
        assert accepted.verifier.agent_id != evidence.agent_id
        assert research.repository.list_verification_bindings(item.research_item_id)[0].subject_id == (
            claim.claim_id
        )

    asyncio.run(scenario())
