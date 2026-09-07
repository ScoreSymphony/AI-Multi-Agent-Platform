from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.research import (
    ClaimConfidence,
    ClaimStatus,
    EvidenceFreshness,
    EvidenceRelation,
    FreshnessPolicy,
    InMemoryResearchRepository,
    ResearchClass,
    ResearchPlanningBridge,
    ResearchService,
    ResearchSourceType,
    ResearchVerificationBridge,
    ResearchVerificationSubjectType,
    SourceObservationState,
    SqliteResearchRepository,
    UntrustedResearchExecutionProfile,
)
from ai_multi_agent_platform.verification import (
    ReviewerIndependence,
    VerificationOutcome,
    VerificationPolicy,
    VerificationService,
    VerificationStage,
    VerifierIdentity,
    VerifierKind,
)


OWNER = OwnerRef(type="user", id="researcher")


def run(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def _supported_research(
    service: ResearchService,
    *,
    task_id: str | None = None,
    freshness_policy: FreshnessPolicy = FreshnessPolicy(),
) -> tuple[str, str, str, str]:
    item = run(
        service.create_item(
            title="Research",
            question="Which exact source supports the claim?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OWNER,
            task_id=task_id,
            freshness_policy=freshness_policy,
        )
    )
    source = run(
        service.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.WEB,
            locator="https://example.test/research",
            title="Primary source",
        )
    )
    observation = run(
        service.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 10, tzinfo=UTC),
            content_digest="sha256:first",
            identity_proven=True,
            idempotency_key="observation-1",
        )
    )
    claim = run(
        service.add_claim(
            item.research_item_id,
            text="The source explicitly documents the behavior.",
            category="technical",
            confidence=ClaimConfidence.HIGH,
        )
    )
    evidence = run(
        service.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
            location_ref="section:behavior",
        )
    )
    assessed = service.assess_claim(claim.claim_id)
    assert assessed.status is ClaimStatus.SUPPORTED
    return item.research_item_id, source.source_id, claim.claim_id, evidence.evidence_id


def test_changed_source_preserves_historical_evidence_and_revalidates() -> None:
    service = ResearchService(InMemoryResearchRepository())
    item_id, source_id, claim_id, evidence_id = _supported_research(service)
    original = service.repository.get_evidence(evidence_id)

    changed = run(
        service.observe_source(
            source_id,
            retrieved_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
            content_digest="sha256:changed",
            identity_proven=True,
            idempotency_key="observation-2",
        )
    )
    assert changed.state is SourceObservationState.CHANGED
    assert service.evidence_freshness(evidence_id) is EvidenceFreshness.STALE
    assert service.repository.get_evidence(evidence_id) == original

    replacement = run(service.revalidate_evidence(evidence_id, changed.observation_id))
    assert replacement.evidence_id != evidence_id
    assert replacement.supersedes_evidence_id == evidence_id
    assert replacement.source_content_digest == "sha256:changed"
    assert service.evidence_freshness(replacement.evidence_id) is EvidenceFreshness.CURRENT
    assert service.repository.get_claim(claim_id).evidence_ids[-1] == replacement.evidence_id
    assert item_id == replacement.research_item_id


def test_observation_idempotency_rejects_changed_content_for_same_key() -> None:
    service = ResearchService(InMemoryResearchRepository())
    item_id, source_id, _, _ = _supported_research(service)
    source = service.repository.get_source(source_id)
    original = service.repository.get_observation(source.observation_ids[0])

    duplicate = run(
        service.observe_source(
            source_id,
            retrieved_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
            content_digest="sha256:first",
            identity_proven=True,
            idempotency_key="observation-1",
        )
    )
    assert duplicate.observation_id == original.observation_id

    with pytest.raises(ContractError) as conflict:
        run(
            service.observe_source(
                source_id,
                retrieved_at=datetime(2026, 9, 8, 13, tzinfo=UTC),
                content_digest="sha256:different",
                identity_proven=True,
                idempotency_key="observation-1",
            )
        )
    assert conflict.value.code is ErrorCode.CONFLICT
    assert service.repository.get_item(item_id).research_item_id == item_id


def test_stale_or_unsupported_claims_cannot_drive_action() -> None:
    service = ResearchService(InMemoryResearchRepository())
    item_id, source_id, claim_id, evidence_id = _supported_research(
        service,
        freshness_policy=FreshnessPolicy(max_age_seconds=60),
    )
    assert (
        service.evidence_freshness(
            evidence_id,
            now=datetime(2026, 9, 8, 10, 2, tzinfo=UTC),
        )
        is EvidenceFreshness.STALE
    )

    run(
        service.observe_source(
            source_id,
            retrieved_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
            content_digest="sha256:contradiction",
            identity_proven=True,
        )
    )
    assert service.assess_claim(claim_id).status is ClaimStatus.PROPOSED
    with pytest.raises(ContractError) as blocked:
        service.build_action_context(item_id, require_verification=False)
    assert blocked.value.code is ErrorCode.CONFLICT


def test_sqlite_restart_preserves_observations_evidence_and_staleness(tmp_path: Path) -> None:
    database = tmp_path / "research.sqlite3"
    first = ResearchService(SqliteResearchRepository(database))
    item_id, source_id, _, evidence_id = _supported_research(first)
    run(
        first.observe_source(
            source_id,
            retrieved_at=datetime(2026, 9, 8, 11, tzinfo=UTC),
            content_digest="sha256:new",
            identity_proven=True,
        )
    )
    before = first.repository.get_evidence(evidence_id)
    assert first.evidence_freshness(evidence_id) is EvidenceFreshness.STALE

    restored = ResearchService(SqliteResearchRepository(database))
    assert restored.repository.get_item(item_id).research_item_id == item_id
    assert restored.repository.get_evidence(evidence_id) == before
    assert restored.evidence_freshness(evidence_id) is EvidenceFreshness.STALE
    assert len(restored.repository.list_observations(source_id)) == 2


def test_reviewer_independence_and_exact_revision_are_inherited_from_issue_86() -> None:
    producer = new_id("agent")
    reviewer = new_id("agent")
    task_id = new_id("task")
    research = ResearchService(InMemoryResearchRepository())
    item = run(
        research.create_item(
            title="Review research",
            question="Is the claim independently reviewed?",
            research_class=ResearchClass.TASK_RESEARCH,
            owner_ref=OWNER,
            task_id=task_id,
        )
    )
    claim = run(
        research.add_claim(
            item.research_item_id,
            text="A claim requiring independent review.",
            category="architecture",
            author_ref=f"agent:{producer}:1",
            agent_id=producer,
            agent_revision=1,
        )
    )

    verification = VerificationService()
    policy = verification.register_policy(
        VerificationPolicy(
            name="independent research review",
            stages=(VerificationStage("research-review", VerifierKind.AGENT),),
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                agent_reviewer_must_be_read_only=True,
            ),
        )
    )
    bridge = ResearchVerificationBridge(research, verification)
    request = bridge.request_verification(
        subject_type=ResearchVerificationSubjectType.CLAIM,
        subject_id=claim.claim_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="research-review",
        correlation_id="research-review",
    )

    with pytest.raises(ContractError) as self_review:
        verification.record_agent_review(
            request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref=f"agent:{producer}:1",
                kind=VerifierKind.AGENT,
                agent_id=producer,
                agent_revision=1,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
        )
    assert self_review.value.code is ErrorCode.FORBIDDEN

    verification.record_agent_review(
        request.verification_id,
        verifier=VerifierIdentity(
            verifier_ref=f"agent:{reviewer}:1",
            kind=VerifierKind.AGENT,
            agent_id=reviewer,
            agent_revision=1,
            read_only=True,
        ),
        outcome=VerificationOutcome.PASS,
    )
    binding = bridge.bind_completed_pass(request.verification_id)
    assert binding.subject_id == claim.claim_id
    assert binding.subject_revision == "1"

    revised = research.repository.save_claim(
        claim.__class__(
            research_item_id=claim.research_item_id,
            text="A materially changed claim.",
            category=claim.category,
            claim_id=claim.claim_id,
            revision=2,
            confidence=claim.confidence,
            status=claim.status,
            author_ref=claim.author_ref,
            agent_id=claim.agent_id,
            agent_revision=claim.agent_revision,
        ),
        expected_revision=1,
    )
    assert revised.digest != request.subject.digest
    with pytest.raises(ContractError):
        bridge.bind_completed_pass(request.verification_id)


def test_untrusted_execution_profile_fails_closed_for_secrets_and_writes() -> None:
    profile = UntrustedResearchExecutionProfile()
    assert profile.isolated_workspace is True
    assert profile.allow_network_egress is False
    assert profile.allow_platform_secrets is False
    assert profile.allow_provider_secrets is False
    assert profile.allow_production_writes is False

    with pytest.raises(ValueError):
        UntrustedResearchExecutionProfile(allow_platform_secrets=True)
    with pytest.raises(ValueError):
        UntrustedResearchExecutionProfile(allow_production_writes=True)


class _CapturingPlanning:
    def __init__(self) -> None:
        self.kwargs: dict[str, object] = {}

    async def propose(self, **kwargs: object) -> object:
        self.kwargs = kwargs
        return object()


def test_research_to_planning_preserves_exact_provenance_refs() -> None:
    task_id = new_id("task")
    research = ResearchService(InMemoryResearchRepository())
    item_id, _, claim_id, evidence_id = _supported_research(research, task_id=task_id)
    planning = _CapturingPlanning()
    bridge = ResearchPlanningBridge(research, planning)  # type: ignore[arg-type]

    run(
        bridge.propose(
            item_id,
            task_id=task_id,
            idempotency_key="research-plan",
            require_verification=False,
        )
    )
    refs = planning.kwargs["evidence_refs"]
    assert isinstance(refs, tuple)
    assert any(str(value).startswith("research-item:") for value in refs)
    assert f"research-claim:{claim_id}" in refs
    assert f"research-evidence:{evidence_id}" in refs
