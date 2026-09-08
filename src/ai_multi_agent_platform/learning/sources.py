"""Explicit source adapters for governed Learning Candidate creation (#595)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.security import RiskClassification

from .models import (
    LearningCandidate,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
)
from .service import LearningService


class LearningSourceBridge:
    """Bind non-authoritative findings to exact source evidence before proposing change."""

    def __init__(
        self,
        learning: LearningService,
        *,
        research: ResearchService | None = None,
    ) -> None:
        self.learning = learning
        self.research = research

    def from_run_failure_pattern(
        self,
        *,
        source_refs: tuple[LearningReference, ...],
        problem: str,
        target: LearningTarget,
        improvement_type: str,
        expected_benefit: str,
        risk: RiskClassification,
        gate_plan: LearningGatePlan,
        creator_ref: str,
        proposed_change: dict[str, JsonValue] | None = None,
        proposed_artifact_ref: LearningReference | None = None,
        evidence_refs: tuple[LearningReference, ...] = (),
        project_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        bound_sources = _require_repeated_pattern(source_refs, "Run failure")
        return self.learning.create_candidate(
            source_type=LearningSourceType.RUN_FAILURE_PATTERN,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=bound_sources,
            evidence_refs=(*bound_sources, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )

    def from_planning_failure_pattern(
        self,
        *,
        source_refs: tuple[LearningReference, ...],
        problem: str,
        target: LearningTarget,
        improvement_type: str,
        expected_benefit: str,
        risk: RiskClassification,
        gate_plan: LearningGatePlan,
        creator_ref: str,
        proposed_change: dict[str, JsonValue] | None = None,
        proposed_artifact_ref: LearningReference | None = None,
        evidence_refs: tuple[LearningReference, ...] = (),
        project_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        bound_sources = _require_repeated_pattern(source_refs, "Planning failure")
        return self.learning.create_candidate(
            source_type=LearningSourceType.PLANNING_FAILURE_PATTERN,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=bound_sources,
            evidence_refs=(*bound_sources, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )

    def from_research_evidence(
        self,
        evidence_id: str,
        *,
        problem: str,
        target: LearningTarget,
        improvement_type: str,
        expected_benefit: str,
        risk: RiskClassification,
        gate_plan: LearningGatePlan,
        creator_ref: str,
        proposed_change: dict[str, JsonValue] | None = None,
        proposed_artifact_ref: LearningReference | None = None,
        evidence_refs: tuple[LearningReference, ...] = (),
    ) -> tuple[LearningCandidate, bool]:
        if self.research is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Research service is not configured")
        evidence = self.research.repository.get_evidence(evidence_id)
        item = self.research.repository.get_item(evidence.research_item_id)
        claim = self.research.repository.get_claim(evidence.claim_id)
        observation = self.research.repository.get_observation(evidence.source_observation_id)
        source_ref = LearningReference(
            kind="research_evidence",
            resource_id=evidence.evidence_id,
            digest=evidence.digest,
        )
        observation_revision = (
            observation.resolved_repository_revision
            or observation.revision
            or observation.version
            or observation.commit
            or observation.etag
        )
        observation_digest = observation.content_digest or observation.snapshot_digest
        supporting_refs = (
            LearningReference(
                kind="research_item",
                resource_id=item.research_item_id,
                revision=str(item.revision),
                digest=item.digest,
            ),
            LearningReference(
                kind="research_claim",
                resource_id=claim.claim_id,
                revision=str(claim.revision),
                digest=claim.digest,
            ),
            LearningReference(
                kind="research_source_observation",
                resource_id=observation.observation_id,
                revision=observation_revision,
                digest=observation_digest,
            ),
        )
        return self.learning.create_candidate(
            source_type=LearningSourceType.RESEARCH_EVIDENCE,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(source_ref,),
            evidence_refs=(source_ref, *supporting_refs, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=item.project_id,
        )

    def operator_proposal(
        self,
        *,
        proposal_ref: LearningReference,
        problem: str,
        target: LearningTarget,
        improvement_type: str,
        expected_benefit: str,
        risk: RiskClassification,
        gate_plan: LearningGatePlan,
        creator_ref: str,
        proposed_change: dict[str, JsonValue] | None = None,
        proposed_artifact_ref: LearningReference | None = None,
        evidence_refs: tuple[LearningReference, ...] = (),
        project_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        return self.learning.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(proposal_ref,),
            evidence_refs=(proposal_ref, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )


def _require_repeated_pattern(
    source_refs: tuple[LearningReference, ...],
    name: str,
) -> tuple[LearningReference, ...]:
    unique: list[LearningReference] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for reference in source_refs:
        if reference.key in seen:
            continue
        seen.add(reference.key)
        unique.append(reference)
    if len(unique) < 2:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} pattern requires at least two distinct concrete source references",
        )
    return tuple(unique)
