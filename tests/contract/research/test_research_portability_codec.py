from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability import ResourceSerializerRegistry
from ai_multi_agent_platform.portability.research_codecs import (
    RESEARCH_BUNDLE_RESOURCE_TYPE,
    PortableResearchBundle,
    portable_research_bundle,
    register_research_bundle_portability_codec,
)
from ai_multi_agent_platform.research import (
    EvidenceRelation,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
    ResearchSourceType,
    ResearchVerificationBridge,
    ResearchVerificationSubjectType,
)
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationResult,
    VerificationService,
    VerificationStage,
    VerifierIdentity,
    VerifierKind,
)


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_issue79_research_codec_preserves_history_and_declares_verification_dependency() -> None:
    research = ResearchService(InMemoryResearchRepository())
    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    task_id = new_id("task")
    item = run(
        research.create_item(
            title="Portable research",
            question="Can exact Research provenance cross the #79 boundary?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OwnerRef(type="user", id="portable-owner"),
            task_id=task_id,
        )
    )
    source = run(
        research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.PAPER,
            locator="doi:10.0000/example",
            title="Portable source",
        )
    )
    observation = run(
        research.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
            content_digest="sha256:portable-source",
            identity_proven=True,
        )
    )
    claim = run(
        research.add_claim(
            item.research_item_id,
            text="Portable history keeps exact evidence bindings.",
            category="portability",
        )
    )
    evidence = run(
        research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
        )
    )
    research.assess_claim(claim.claim_id)

    policy = verification.register_policy(
        VerificationPolicy(
            name="portable claim review",
            stages=(VerificationStage("review", VerifierKind.HUMAN),),
        )
    )
    bridge = ResearchVerificationBridge(research, verification)
    request = bridge.request_verification(
        subject_type=ResearchVerificationSubjectType.CLAIM,
        subject_id=claim.claim_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        correlation_id="portable-claim-review",
    )
    bridge.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="user:portable-reviewer",
                kind=VerifierKind.HUMAN,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            checks_executed=("portable-review",),
        )
    )

    registry = ResourceSerializerRegistry()
    register_research_bundle_portability_codec(registry)
    portable = registry.serialize(
        RESEARCH_BUNDLE_RESOURCE_TYPE,
        portable_research_bundle(research, item.research_item_id),
    )
    assert portable.resource_id == item.research_item_id
    assert portable.id_policy.value == "historical_preserve"
    verification_dependencies = [
        dependency
        for dependency in portable.dependencies
        if dependency.identifier == f"verification:{request.verification_id}"
    ]
    assert len(verification_dependencies) == 1
    assert verification_dependencies[0].required is True

    decoded = registry.deserialize(portable)
    assert isinstance(decoded, PortableResearchBundle)
    assert decoded.research_item_id == item.research_item_id
    assert decoded.bundle["activation_semantics"] == "none"
    assert evidence.evidence_id in repr(decoded.bundle)
