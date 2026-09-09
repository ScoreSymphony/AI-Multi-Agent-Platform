from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.learning import (
    InMemoryLearningRepository,
    LearningGatePlan,
    LearningQualityGate,
    LearningReference,
    LearningService,
    LearningSourceBridge,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    PromotionRegistry,
)
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.research.models import (
    Claim,
    EvidenceRecord,
    EvidenceRelation,
    ResearchClass,
    ResearchItem,
    ResearchSourceType,
    SourceObservation,
    SourceRecord,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    RiskClassification,
)

OWNER = OwnerRef(type="user", id="issue-694-owner")


class _ResearchRepositoryStub:
    def __init__(
        self,
        *,
        item: ResearchItem,
        claim: Claim,
        source: SourceRecord,
        observation: SourceObservation,
        evidence: EvidenceRecord,
    ) -> None:
        self.item = item
        self.claim = claim
        self.source = source
        self.observation = observation
        self.evidence = evidence

    def get_item(self, resource_id: str) -> ResearchItem:
        assert resource_id == self.item.research_item_id
        return self.item

    def get_claim(self, resource_id: str) -> Claim:
        assert resource_id == self.claim.claim_id
        return self.claim

    def get_source(self, resource_id: str) -> SourceRecord:
        assert resource_id == self.source.source_id
        return self.source

    def get_observation(self, resource_id: str) -> SourceObservation:
        assert resource_id == self.observation.observation_id
        return self.observation

    def get_evidence(self, resource_id: str) -> EvidenceRecord:
        assert resource_id == self.evidence.evidence_id
        return self.evidence


def _learning() -> LearningService:
    return LearningService(
        InMemoryLearningRepository(),
        quality_gate=LearningQualityGate(),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=AuthorizationGate(LocalAuthorizationProvider(())),
    )


def _target() -> LearningTarget:
    return LearningTarget(
        resource_type=LearningTargetType.AGENT,
        resource_id=new_id("agent"),
        revision=1,
    )


def _gate() -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="issue-694",
        policy_version=1,
        require_evaluation=True,
        evaluation_suite_refs=("issue-694-suite@1",),
    )


def _research_fixture(project_id: str) -> tuple[ResearchService, EvidenceRecord]:
    item_id = new_id("research_item")
    claim_id = new_id("research_claim")
    source_id = new_id("research_source")
    observation_id = new_id("research_observation")
    evidence_id = new_id("research_evidence")
    item = ResearchItem(
        title="Issue 694 research",
        question="Which bounded method is supported?",
        research_class=ResearchClass.PROJECT_RESEARCH,
        owner_ref=OWNER,
        research_item_id=item_id,
        project_id=project_id,
        source_ids=(source_id,),
        claim_ids=(claim_id,),
        evidence_ids=(evidence_id,),
    )
    claim = Claim(
        research_item_id=item_id,
        text="Method B is better supported.",
        category="method",
        claim_id=claim_id,
        evidence_ids=(evidence_id,),
    )
    source = SourceRecord(
        research_item_id=item_id,
        source_type=ResearchSourceType.DOCUMENT,
        locator="artifact://issue-694-source",
        title="Issue 694 source",
        source_id=source_id,
        current_observation_id=observation_id,
        observation_ids=(observation_id,),
    )
    observation = SourceObservation(
        source_id=source_id,
        research_item_id=item_id,
        retrieved_at=datetime.now(UTC),
        observation_id=observation_id,
        revision="r7",
        content_digest="sha256:observation-694",
        identity_proven=True,
    )
    evidence = EvidenceRecord(
        research_item_id=item_id,
        source_id=source_id,
        source_observation_id=observation_id,
        claim_id=claim_id,
        relation=EvidenceRelation.SUPPORTS,
        retrieved_at=datetime.now(UTC),
        evidence_id=evidence_id,
        source_revision="r7",
        source_content_digest="sha256:observation-694",
    )
    repository = _ResearchRepositoryStub(
        item=item,
        claim=claim,
        source=source,
        observation=observation,
        evidence=evidence,
    )
    return cast(ResearchService, SimpleNamespace(repository=repository)), evidence


def _research_candidate(
    bridge: LearningSourceBridge,
    evidence_id: str,
    project_id: str,
):
    return bridge.from_research_evidence(
        evidence_id,
        problem="Research identified a better bounded method.",
        target=_target(),
        improvement_type="owner_revision",
        expected_benefit="Use evidence-backed behavior.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate(),
        creator_ref="user:issue-694",
        proposed_change={"description": "Use the evidence-backed method."},
        project_id=project_id,
    )


def test_research_source_preserves_full_canonical_chain() -> None:
    project_id = new_id("project")
    research, evidence = _research_fixture(project_id)
    bridge = LearningSourceBridge(_learning(), research=research)

    candidate, created = _research_candidate(
        bridge,
        evidence.evidence_id,
        project_id,
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.RESEARCH_EVIDENCE
    refs = {reference.kind: reference for reference in candidate.evidence_refs}
    assert refs["research_evidence"].digest == evidence.digest
    assert refs["research_item"].revision == "1"
    assert refs["research_claim"].revision == "1"
    assert refs["research_source"].resource_id == evidence.source_id
    assert refs["research_source_observation"].revision == "r7"
    assert refs["research_source_observation"].digest == "sha256:observation-694"


def test_broken_research_chain_is_rejected() -> None:
    project_id = new_id("project")
    research, evidence = _research_fixture(project_id)
    repository = cast(_ResearchRepositoryStub, research.repository)
    repository.claim = replace(
        repository.claim,
        research_item_id=new_id("research_item"),
    )
    bridge = LearningSourceBridge(_learning(), research=research)

    with pytest.raises(ContractError) as error:
        _research_candidate(bridge, evidence.evidence_id, project_id)

    assert error.value.code is ErrorCode.CONTRACT_VIOLATION


def test_research_project_mismatch_is_rejected() -> None:
    project_id = new_id("project")
    research, evidence = _research_fixture(project_id)
    bridge = LearningSourceBridge(_learning(), research=research)

    with pytest.raises(ContractError) as error:
        _research_candidate(bridge, evidence.evidence_id, new_id("project"))

    assert error.value.code is ErrorCode.FORBIDDEN


def test_operator_proposal_cannot_impersonate_trusted_source_kind() -> None:
    bridge = LearningSourceBridge(_learning())

    with pytest.raises(ContractError) as spoofed:
        bridge.operator_proposal(
            proposal_ref=LearningReference(
                kind="verification_result",
                resource_id="verification-result-spoof",
            ),
            problem="Spoof a trusted source.",
            target=_target(),
            improvement_type="owner_revision",
            expected_benefit="none",
            risk=RiskClassification.STANDARD,
            gate_plan=_gate(),
            creator_ref="user:issue-694",
        )
    assert spoofed.value.code is ErrorCode.INVALID_REQUEST

    candidate, created = bridge.operator_proposal(
        proposal_ref=LearningReference(
            kind="operator_proposal",
            resource_id="proposal-694",
        ),
        problem="Operator-authored improvement.",
        target=_target(),
        improvement_type="owner_revision",
        expected_benefit="Keep provenance explicit.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate(),
        creator_ref="user:issue-694",
    )
    assert created is True
    assert candidate.source_type is LearningSourceType.OPERATOR_PROPOSAL
    assert candidate.source_refs[0].kind == "operator_proposal"


def test_public_learning_propose_remains_operator_source_type(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(
                data_dir=tmp_path / "public-boundary",
                secure_cookie=False,
            )
        )
        project = deployment.scopes.create_project(
            key="issue-694-project",
            name="Issue 694 project",
            owner_type="user",
            owner_id=OWNER.id,
        )
        agent = deployment.agents.create_agent(
            AgentProfile(
                name="Issue 694 Agent",
                role="worker",
                description="Source-boundary target.",
                instructions=AgentInstructions(
                    role=InstructionSource(
                        content="Use the bounded method.",
                        version="1",
                    )
                ),
            ),
            owner_ref=OWNER,
            project_id=project.id,
        )
        principal = "user:issue-694-operator"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset(
                    {
                        AuthorizationAction.CREATE,
                        AuthorizationAction.MODIFY,
                    }
                ),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project.id}),
            )
        )
        context = RequestContext(
            request_id="issue-694-propose",
            correlation_id="issue-694-propose",
            idempotency_key="issue-694-propose",
            actor=ActorContext(
                principal_ref=principal,
                actor_type=ActorType.HUMAN.value,
            ),
        )

        created = await deployment.control_plane.execute_command(
            context,
            "learning.propose",
            "learning-candidates",
            {
                "source_type": "verification",
                "problem": "Operator-authored issue 694 proposal.",
                "target": {
                    "resource_type": "agent",
                    "resource_id": agent.agent_id,
                    "revision": agent.revision,
                },
                "improvement_type": "owner_revision",
                "expected_benefit": "Preserve source authority boundaries.",
                "risk": "standard",
                "gate_plan": {
                    "policy_id": "issue-694",
                    "policy_version": 1,
                    "require_evaluation": True,
                    "require_verification": False,
                    "require_regression_free": True,
                    "evaluation_suite_refs": ["issue-694-suite@1"],
                    "verification_policy_refs": [],
                },
                "source_refs": [
                    {
                        "kind": "operator_proposal",
                        "resource_id": "public-proposal-694",
                    }
                ],
                "proposed_change": {"description": "Bounded change."},
                "project_id": project.id,
            },
        )

        assert created["source_type"] == LearningSourceType.OPERATOR_PROPOSAL.value
        candidate = deployment.learning.service.get_candidate(str(created["id"]))
        assert candidate.source_type is LearningSourceType.OPERATOR_PROPOSAL

    asyncio.run(scenario())
