from __future__ import annotations

import asyncio

from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.research import (
    InMemoryResearchRepository,
    ResearchClass,
    ResearchService,
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


OWNER = OwnerRef(type="user", id="researcher")


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_research_verification_works_in_strict_canonical_mode() -> None:
    task_id = new_id("task")
    research = ResearchService(InMemoryResearchRepository())
    item = run(
        research.create_item(
            title="Strict verification research",
            question="Does Research work with production-shaped strict Verification?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OWNER,
            task_id=task_id,
        )
    )
    claim = run(
        research.add_claim(
            item.research_item_id,
            text="Strict canonical Verification accepts a platform-resolved Research Claim.",
            category="verification",
        )
    )

    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    policy = verification.register_policy(
        VerificationPolicy(
            name="strict research human review",
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
        correlation_id="strict-research-review",
    )
    submitted = bridge.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="user:independent-reviewer",
                kind=VerifierKind.HUMAN,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=request.subject,
            checks_executed=("human_review",),
        )
    )
    assert submitted.outcome is VerificationOutcome.PASS
    bindings = research.repository.list_verification_bindings(item.research_item_id)
    assert len(bindings) == 1
    assert bindings[0].verification_id == request.verification_id
    assert bindings[0].subject_id == claim.claim_id
