from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.evaluation import (
    ConfigurationSnapshot,
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationExecutionContext,
    EvaluationOutcome,
    EvaluationRunner,
    InMemoryEvaluationRepository,
    MetricThresholdEvaluator,
    ResearchEvaluationCaseExecutor,
    canonical_research_quality_suite,
)
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


async def _seed_verified_research() -> tuple[
    ResearchService,
    str,
    str,
    str,
    str,
]:
    research = ResearchService(InMemoryResearchRepository())
    task_id = new_id("task")
    item = await research.create_item(
        title="Verified research quality fixture",
        question="Does the canonical Research record satisfy decision-readiness quality gates?",
        research_class=ResearchClass.TASK_RESEARCH,
        owner_ref=OwnerRef(type="user", id="research-evaluation-owner"),
        task_id=task_id,
    )
    source = await research.add_source(
        item.research_item_id,
        source_type=ResearchSourceType.DOCUMENT,
        locator="artifact://research-fixture/source-a",
        title="Primary fixture source",
        trust_classification="primary",
        metadata={"provider_id": "replaceable-source-provider-a"},
    )
    observation = await research.observe_source(
        source.source_id,
        retrieved_at=datetime(2026, 9, 8, 7, 0, tzinfo=UTC),
        content_digest="sha256:fixture-source-v1",
        snapshot_digest="sha256:fixture-snapshot-v1",
        identity_proven=True,
    )
    claim = await research.add_claim(
        item.research_item_id,
        text="Current exact evidence supports the Research quality fixture.",
        category="research-quality",
        agent_id=_RESEARCHER_ID,
        agent_revision=1,
    )
    evidence = await research.add_evidence(
        claim.claim_id,
        observation.observation_id,
        relation=EvidenceRelation.SUPPORTS,
        agent_id=_RESEARCHER_ID,
        agent_revision=1,
        excerpt_digest="sha256:fixture-excerpt-v1",
    )
    research.assess_claim(claim.claim_id)

    verification = VerificationService(
        require_canonical_subjects=True,
        require_canonical_results=True,
    )
    policy = verification.register_policy(
        VerificationPolicy(
            name="independent Research Team review",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            independence=ReviewerIndependence(
                producer_agent_must_differ=True,
                agent_reviewer_must_be_read_only=True,
                forbid_self_verification=True,
            ),
        )
    )
    bridge = ResearchVerificationBridge(research, verification)
    for subject_type, subject_id in (
        (ResearchVerificationSubjectType.CLAIM, claim.claim_id),
        (ResearchVerificationSubjectType.EVIDENCE, evidence.evidence_id),
    ):
        request = bridge.request_verification(
            subject_type=subject_type,
            subject_id=subject_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            stage_id="review",
            correlation_id=f"research-evaluation:{subject_id}",
        )
        bridge.submit_result(
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
                checks_executed=("source_binding", "claim_support"),
            )
        )

    return (
        research,
        item.research_item_id,
        source.source_id,
        observation.observation_id,
        evidence.evidence_id,
    )


def test_verified_current_research_passes_deterministic_quality_suite() -> None:
    async def scenario() -> None:
        research, item_id, _, _, _ = await _seed_verified_research()
        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=ResearchEvaluationCaseExecutor(research),
            evaluators=(
                DeterministicAssertionEvaluator(),
                MetricThresholdEvaluator(),
            ),
        )
        summary = await runner.run_suite(
            suite=canonical_research_quality_suite(item_id),
            snapshot=ConfigurationSnapshot(platform_version="0.0.1"),
        )

        assert summary.run.status.value == "completed"
        assert len(summary.results) == 2
        assert {result.outcome for result in summary.results} == {EvaluationOutcome.PASSED}

        metric_result = next(
            result
            for result in summary.results
            if result.evaluator.evaluator_id == "reference.metric-threshold"
        )
        assert {metric.metric_name for metric in metric_result.metrics} == {
            "citation_coverage",
            "supported_claim_rate",
            "unsupported_claim_rate",
            "disputed_claim_rate",
            "stale_evidence_rate",
            "unavailable_evidence_rate",
            "unverifiable_evidence_rate",
            "source_binding_integrity_rate",
            "verification_coverage",
        }
        assert all(metric.passed for metric in metric_result.metrics)

    asyncio.run(scenario())


def test_executor_exposes_reproducible_research_quality_evidence() -> None:
    async def scenario() -> None:
        research, item_id, _, _, evidence_id = await _seed_verified_research()
        case = canonical_research_quality_suite(item_id).cases[0]
        attempt = EvaluationAttempt(
            evaluation_run_id="evaluation_run_research_589",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
        )
        observation = await ResearchEvaluationCaseExecutor(research).execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )

        assert observation.data["claim_count"] == 1
        assert observation.data["evidence_count"] == 1
        assert observation.data["supported_claim_count"] == 1
        assert observation.data["unsupported_claim_count"] == 0
        assert observation.data["disputed_claim_count"] == 0
        assert observation.data["source_binding_integrity_valid"] is True
        assert observation.data["current_verification_count"] == 2
        assert observation.metrics["citation_coverage"] == 1.0
        assert observation.metrics["current_evidence_rate"] == 1.0
        assert observation.metrics["source_binding_integrity_rate"] == 1.0
        assert observation.metrics["verification_coverage"] == 1.0
        assert evidence_id in {
            value.evidence_id for value in research.repository.list_evidence(item_id)
        }
        assert observation.capability_refs == ()

    asyncio.run(scenario())


def test_changed_source_fails_freshness_gate_without_rewriting_historical_evidence() -> None:
    async def scenario() -> None:
        (
            research,
            item_id,
            source_id,
            original_observation_id,
            evidence_id,
        ) = await _seed_verified_research()
        historical = research.repository.get_evidence(evidence_id)
        assert historical.source_observation_id == original_observation_id
        assert historical.source_content_digest == "sha256:fixture-source-v1"

        changed = await research.observe_source(
            source_id,
            retrieved_at=datetime(2026, 9, 8, 8, 0, tzinfo=UTC),
            content_digest="sha256:fixture-source-v2",
            snapshot_digest="sha256:fixture-snapshot-v2",
            identity_proven=True,
        )
        assert changed.state.value == "changed"

        runner = EvaluationRunner(
            repository=InMemoryEvaluationRepository(),
            executor=ResearchEvaluationCaseExecutor(research),
            evaluators=(MetricThresholdEvaluator(),),
        )
        summary = await runner.run_suite(
            suite=canonical_research_quality_suite(item_id),
            snapshot=ConfigurationSnapshot(platform_version="0.0.1"),
        )

        assert summary.results[0].outcome is EvaluationOutcome.FAILED
        metrics = {metric.metric_name: metric for metric in summary.results[0].metrics}
        assert metrics["stale_evidence_rate"].value == 1.0
        assert metrics["stale_evidence_rate"].passed is False
        assert metrics["supported_claim_rate"].value == 0.0
        assert research.repository.get_evidence(evidence_id) == historical

    asyncio.run(scenario())


def test_provider_metadata_replacement_does_not_change_research_quality_semantics() -> None:
    async def quality(provider_id: str) -> tuple[dict[str, object], dict[str, float]]:
        research = ResearchService(InMemoryResearchRepository())
        item = await research.create_item(
            title="Provider replacement fixture",
            question="Does provider identity alter canonical Research quality?",
            research_class=ResearchClass.PROJECT_RESEARCH,
            owner_ref=OwnerRef(type="user", id="provider-replacement-owner"),
        )
        source = await research.add_source(
            item.research_item_id,
            source_type=ResearchSourceType.API_RESPONSE,
            locator="provider://canonical-source",
            title="Replaceable provider source",
            metadata={"provider_id": provider_id},
        )
        observation = await research.observe_source(
            source.source_id,
            retrieved_at=datetime(2026, 9, 8, 7, 0, tzinfo=UTC),
            content_digest="sha256:provider-neutral-content",
            identity_proven=True,
        )
        claim = await research.add_claim(
            item.research_item_id,
            text="Provider replacement preserves the canonical semantic model.",
            category="provider-replacement",
        )
        await research.add_evidence(
            claim.claim_id,
            observation.observation_id,
            relation=EvidenceRelation.SUPPORTS,
        )
        case = canonical_research_quality_suite(item.research_item_id).cases[0]
        attempt = EvaluationAttempt(
            evaluation_run_id=f"evaluation_run_{provider_id.replace('.', '_')}",
            case_id=case.case_id,
            case_version=case.version,
            repetition_index=0,
        )
        result = await ResearchEvaluationCaseExecutor(research).execute_case(
            case=case,
            attempt=attempt,
            execution_context=EvaluationExecutionContext(attempt_id=attempt.attempt_id),
        )
        comparable_data = {
            key: value for key, value in result.data.items() if key not in {"research_item_id"}
        }
        return comparable_data, dict(result.metrics)

    baseline = asyncio.run(quality("provider.baseline"))
    replacement = asyncio.run(quality("provider.replacement"))
    assert replacement == baseline
