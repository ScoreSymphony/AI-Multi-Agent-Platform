"""Deterministic #19 evaluation adapter for canonical Research Evidence (#589)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.research import (
    Claim,
    EvidenceFreshness,
    EvidenceRecord,
    EvidenceRelation,
    ResearchService,
    ResearchVerificationSubjectType,
)

from .context import EvaluationExecutionContext
from .models import (
    ComparisonOperator,
    DeterministicAssertion,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationObservation,
    EvaluationSuite,
    MetricRule,
)

RESEARCH_QUALITY_SUITE_ID = "suite.research-quality"
RESEARCH_QUALITY_SUITE_VERSION = "1"


class ResearchEvaluationCaseExecutor:
    """Project canonical Research quality into the existing deterministic #19 framework.

    The executor reads only Research-owned structured records. It does not invoke an LLM,
    mutate Claim status, perform source acquisition, or create Verification results. This makes
    the same versioned EvaluationCase reusable across Agent/model/provider replacements.
    """

    def __init__(self, research: ResearchService) -> None:
        self._research = research

    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        if execution_context.attempt_id != attempt.attempt_id:
            raise ValueError("evaluation execution context belongs to another attempt")

        research_item_id = _research_item_id(case)
        item = self._research.repository.get_item(research_item_id)
        claims = self._research.repository.list_claims(research_item_id)
        evidence = self._research.repository.list_evidence(research_item_id)
        freshness = {
            value.evidence_id: self._research.evidence_freshness(value.evidence_id)
            for value in evidence
        }

        claim_quality = tuple(_claim_quality(claim, evidence, freshness) for claim in claims)
        supported_claim_count = sum(value == "supported" for value in claim_quality)
        disputed_claim_count = sum(value == "disputed" for value in claim_quality)
        unsupported_claim_count = sum(value == "unsupported" for value in claim_quality)
        citation_covered_claim_count = sum(bool(claim.evidence_ids) for claim in claims)

        current_evidence_count = sum(
            value is EvidenceFreshness.CURRENT for value in freshness.values()
        )
        stale_evidence_count = sum(value is EvidenceFreshness.STALE for value in freshness.values())
        unavailable_evidence_count = sum(
            value is EvidenceFreshness.UNAVAILABLE for value in freshness.values()
        )
        unverifiable_evidence_count = sum(
            value is EvidenceFreshness.UNVERIFIABLE for value in freshness.values()
        )
        contradiction_count = sum(
            value.relation is EvidenceRelation.CONTRADICTS for value in evidence
        )

        binding_valid_count = sum(
            _source_binding_valid(self._research, value) for value in evidence
        )
        current_verification_count = _current_verification_count(
            self._research,
            research_item_id,
            claims,
            evidence,
        )
        verifiable_subject_count = len(claims) + len(evidence)

        data: dict[str, JsonValue] = {
            "research_item_id": item.research_item_id,
            "research_item_revision": item.revision,
            "research_class": item.research_class.value,
            "claim_count": len(claims),
            "evidence_count": len(evidence),
            "has_claims": bool(claims),
            "has_evidence": bool(evidence),
            "supported_claim_count": supported_claim_count,
            "disputed_claim_count": disputed_claim_count,
            "unsupported_claim_count": unsupported_claim_count,
            "citation_covered_claim_count": citation_covered_claim_count,
            "current_evidence_count": current_evidence_count,
            "stale_evidence_count": stale_evidence_count,
            "unavailable_evidence_count": unavailable_evidence_count,
            "unverifiable_evidence_count": unverifiable_evidence_count,
            "contradiction_count": contradiction_count,
            "source_binding_valid_count": binding_valid_count,
            "source_binding_integrity_valid": (
                bool(evidence) and binding_valid_count == len(evidence)
            ),
            "current_verification_count": current_verification_count,
            "verifiable_subject_count": verifiable_subject_count,
        }
        metrics = {
            "citation_coverage": _ratio(citation_covered_claim_count, len(claims)),
            "supported_claim_rate": _ratio(supported_claim_count, len(claims)),
            "unsupported_claim_rate": _ratio(unsupported_claim_count, len(claims)),
            "disputed_claim_rate": _ratio(disputed_claim_count, len(claims)),
            "current_evidence_rate": _ratio(current_evidence_count, len(evidence)),
            "stale_evidence_rate": _ratio(stale_evidence_count, len(evidence)),
            "unavailable_evidence_rate": _ratio(unavailable_evidence_count, len(evidence)),
            "unverifiable_evidence_rate": _ratio(unverifiable_evidence_count, len(evidence)),
            "source_binding_integrity_rate": _ratio(binding_valid_count, len(evidence)),
            "verification_coverage": _ratio(
                current_verification_count,
                verifiable_subject_count,
            ),
        }
        return EvaluationObservation(
            data=data,
            metrics=metrics,
            task_id=item.task_id,
            run_id=item.run_id,
            artifact_refs=tuple(
                sorted({value.artifact_id for value in evidence if value.artifact_id is not None})
            ),
        )


def canonical_research_quality_suite(research_item_id: str) -> EvaluationSuite:
    """Return the strict no-paid-service Research quality fixture for one canonical item."""

    if not research_item_id.strip():
        raise ValueError("research_item_id must not be blank")
    return EvaluationSuite(
        suite_id=RESEARCH_QUALITY_SUITE_ID,
        name="Canonical Research evidence quality",
        version=RESEARCH_QUALITY_SUITE_VERSION,
        description=(
            "Deterministic citation, freshness, contradiction, source-binding and Verification "
            "quality gates for canonical Research Evidence."
        ),
        tags=("research", "issue-589", "deterministic", "no-paid-service"),
        cases=(
            EvaluationCase(
                case_id="case.research-decision-readiness",
                name="Research evidence decision-readiness",
                version="1",
                input_template={"research_item_id": research_item_id},
                tags=("research", "decision-readiness", "provenance"),
                category="research-quality",
                assertions=(
                    DeterministicAssertion(
                        assertion_id="research-item-id",
                        path="research_item_id",
                        operator=ComparisonOperator.EQ,
                        expected=research_item_id,
                    ),
                    DeterministicAssertion(
                        assertion_id="has-claims",
                        path="has_claims",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                    DeterministicAssertion(
                        assertion_id="has-evidence",
                        path="has_evidence",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                    DeterministicAssertion(
                        assertion_id="source-binding-integrity",
                        path="source_binding_integrity_valid",
                        operator=ComparisonOperator.EQ,
                        expected=True,
                    ),
                ),
                metric_rules=(
                    MetricRule(
                        rule_id="citation-coverage",
                        metric_name="citation_coverage",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="supported-claim-rate",
                        metric_name="supported_claim_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="unsupported-claim-rate",
                        metric_name="unsupported_claim_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="disputed-claim-rate",
                        metric_name="disputed_claim_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="stale-evidence-rate",
                        metric_name="stale_evidence_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="unavailable-evidence-rate",
                        metric_name="unavailable_evidence_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="unverifiable-evidence-rate",
                        metric_name="unverifiable_evidence_rate",
                        operator=ComparisonOperator.LTE,
                        threshold=0.0,
                    ),
                    MetricRule(
                        rule_id="source-binding-integrity-rate",
                        metric_name="source_binding_integrity_rate",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                    MetricRule(
                        rule_id="verification-coverage",
                        metric_name="verification_coverage",
                        operator=ComparisonOperator.GTE,
                        threshold=1.0,
                    ),
                ),
            ),
        ),
    )


def _research_item_id(case: EvaluationCase) -> str:
    value = case.input_template.get("research_item_id")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Research evaluation case requires input_template.research_item_id")
    return value


def _claim_quality(
    claim: Claim,
    evidence: tuple[EvidenceRecord, ...],
    freshness: dict[str, EvidenceFreshness],
) -> str:
    current = tuple(
        value
        for value in evidence
        if value.claim_id == claim.claim_id
        and freshness[value.evidence_id] is EvidenceFreshness.CURRENT
    )
    supports = any(value.relation is EvidenceRelation.SUPPORTS for value in current)
    contradicts = any(value.relation is EvidenceRelation.CONTRADICTS for value in current)
    if contradicts:
        return "disputed"
    if supports:
        return "supported"
    return "unsupported"


def _source_binding_valid(research: ResearchService, evidence: EvidenceRecord) -> bool:
    observation = research.repository.get_observation(evidence.source_observation_id)
    return (
        evidence.source_id == observation.source_id
        and evidence.retrieved_at == observation.retrieved_at
        and evidence.source_revision == observation.revision
        and evidence.source_version == observation.version
        and evidence.source_commit == observation.commit
        and evidence.source_etag == observation.etag
        and evidence.source_content_digest == observation.content_digest
        and evidence.source_snapshot_digest == observation.snapshot_digest
    )


def _current_verification_count(
    research: ResearchService,
    research_item_id: str,
    claims: tuple[Claim, ...],
    evidence: tuple[EvidenceRecord, ...],
) -> int:
    required = {
        (
            ResearchVerificationSubjectType.CLAIM,
            claim.claim_id,
            str(claim.revision),
            claim.digest,
        )
        for claim in claims
    }
    required.update(
        (
            ResearchVerificationSubjectType.EVIDENCE,
            value.evidence_id,
            value.source_observation_id,
            value.digest,
        )
        for value in evidence
    )
    matched = {
        (
            binding.subject_type,
            binding.subject_id,
            binding.subject_revision,
            binding.subject_digest,
        )
        for binding in research.repository.list_verification_bindings(research_item_id)
    }
    return len(required & matched)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


__all__ = [
    "RESEARCH_QUALITY_SUITE_ID",
    "RESEARCH_QUALITY_SUITE_VERSION",
    "ResearchEvaluationCaseExecutor",
    "canonical_research_quality_suite",
]
