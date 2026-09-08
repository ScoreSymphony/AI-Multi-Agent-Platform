from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.decisions import (
    DecisionAlternative,
    DecisionAlternativeStatus,
    DecisionOutcome,
    DecisionService,
    SqliteDecisionRepository,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.research import (
    EvidenceRelation,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchDecisionBridge,
    ResearchService,
    ResearchSourceType,
    ResearchVerificationBridge,
    ResearchVerificationSubjectType,
    canonical_verification_binding_validator,
    export_research_bundle,
    import_research_bundle,
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


def _seed_supported_research(
    *,
    verification: VerificationService | None = None,
) -> tuple[ResearchService, str]:
    research = ResearchService(InMemoryResearchRepository())
    task_id = new_id("task")
    item = run(
        research.create_item(
            title="Governed architecture research",
            question="Which architecture is supported by current evidence?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OwnerRef(type="user", id="research-owner"),
            task_id=task_id,
        )
    )
    source = run(
        research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.DOCUMENT,
            locator="artifact://architecture-fixture",
            title="Architecture fixture",
            trust_classification="primary",
        )
    )
    observation = run(
        research.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
            content_digest="sha256:architecture-fixture",
            identity_proven=True,
        )
    )
    claim = run(
        research.add_claim(
            item.research_item_id,
            text="The adapter architecture preserves provider replaceability.",
            category="architecture",
        )
    )
    evidence = run(
        research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
            location_ref="section:adapter-boundary",
            excerpt_digest="sha256:adapter-boundary",
        )
    )
    research.assess_claim(claim.claim_id)

    if verification is not None:
        policy = verification.register_policy(
            VerificationPolicy(
                name="portable research review",
                stages=(VerificationStage("review", VerifierKind.HUMAN),),
            )
        )
        bridge = ResearchVerificationBridge(research, verification)
        for index, (subject_type, subject_id) in enumerate(
            (
                (ResearchVerificationSubjectType.CLAIM, claim.claim_id),
                (ResearchVerificationSubjectType.EVIDENCE, evidence.evidence_id),
            )
        ):
            request = bridge.request_verification(
                subject_type=subject_type,
                subject_id=subject_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                stage_id="review",
                correlation_id=f"research-portability-{index}",
            )
            bridge.submit_result(
                VerificationResult(
                    verification_id=request.verification_id,
                    verifier=VerifierIdentity(
                        verifier_ref=f"user:reviewer-{index}",
                        kind=VerifierKind.HUMAN,
                        read_only=True,
                    ),
                    outcome=VerificationOutcome.PASS,
                    subject=request.subject,
                    checks_executed=("source-and-claim-review",),
                )
            )
    return research, item.research_item_id


def test_decision_bridge_requires_current_verified_research_by_default(tmp_path: Path) -> None:
    research, item_id = _seed_supported_research()
    decisions = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    bridge = ResearchDecisionBridge(research, decisions)

    with pytest.raises(ContractError) as blocked:
        bridge.create_decision(
            item_id,
            title="Architecture choice",
            subject="agent orchestration",
            category="architecture",
            scope_type="platform",
            question="Which architecture should be adopted?",
            alternatives=(
                DecisionAlternative(
                    label="Adapter boundary",
                    status=DecisionAlternativeStatus.SELECTED,
                ),
            ),
            outcome=DecisionOutcome.ADOPT,
            rationale="Current Research supports the adapter boundary.",
            actor_ref="user:architect",
        )

    assert blocked.value.code is ErrorCode.CONFLICT
    assert decisions.list_views() == ()


def test_verified_research_creates_exact_decision_provenance(tmp_path: Path) -> None:
    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    research, item_id = _seed_supported_research(verification=verification)
    decisions = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    bridge = ResearchDecisionBridge(research, decisions)

    created = bridge.create_decision(
        item_id,
        title="Architecture choice",
        subject="agent orchestration",
        category="architecture",
        scope_type="platform",
        question="Which architecture should be adopted?",
        alternatives=(
            DecisionAlternative(
                label="Adapter boundary",
                status=DecisionAlternativeStatus.SELECTED,
            ),
        ),
        outcome=DecisionOutcome.ADOPT,
        rationale="Current independently verified Research supports the adapter boundary.",
        actor_ref="user:architect",
        reviewer_refs=("user:reviewer-0", "user:reviewer-1"),
    )

    item = research.repository.get_item(item_id)
    assert created.record.subject_ref is not None
    assert created.record.subject_ref.kind == "research-item"
    assert created.record.subject_ref.resource_id == item_id
    assert created.record.subject_ref.revision == item.revision
    assert created.record.subject_ref.digest == item.digest
    kinds = {reference.kind for reference in created.record.evidence_refs}
    assert kinds == {"research-claim", "research-evidence", "verification"}


def test_research_bundle_import_preserves_exact_source_provenance() -> None:
    source, item_id = _seed_supported_research()
    bundle = export_research_bundle(source, item_id)
    target = ResearchService(InMemoryResearchRepository())

    imported_id = import_research_bundle(target, bundle)

    assert imported_id == item_id
    original_item = source.repository.get_item(item_id)
    imported_item = target.repository.get_item(item_id)
    assert imported_item == original_item
    assert imported_item.digest == original_item.digest
    original_evidence = source.repository.get_evidence(original_item.evidence_ids[0])
    imported_evidence = target.repository.get_evidence(imported_item.evidence_ids[0])
    imported_observation = target.repository.get_observation(imported_evidence.source_observation_id)
    assert imported_evidence == original_evidence
    assert imported_evidence.source_content_digest == imported_observation.content_digest
    assert imported_evidence.source_snapshot_digest == imported_observation.snapshot_digest
    assert target.build_action_context(item_id, require_verification=False).research_item_digest == (
        original_item.digest
    )


def test_portable_verification_bindings_fail_closed_without_local_issue86_authority() -> None:
    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    source, item_id = _seed_supported_research(verification=verification)
    bundle = export_research_bundle(source, item_id)
    target = ResearchService(InMemoryResearchRepository())

    with pytest.raises(ContractError) as blocked:
        import_research_bundle(target, bundle)
    assert blocked.value.code is ErrorCode.CONFLICT

    imported = import_research_bundle(
        target,
        bundle,
        verification_binding_validator=canonical_verification_binding_validator(verification),
    )
    assert imported == item_id
    context = target.build_action_context(item_id, require_verification=True)
    assert context.verification_ids
