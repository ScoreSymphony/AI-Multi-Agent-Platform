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
        source_ref: LearningReference,
        repeated_failure_count: int,
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
        _require_repeated_pattern(repeated_failure_count, "Run failure")
        return self.learning.create_candidate(
            source_type=LearningSourceType.RUN_FAILURE_PATTERN,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(source_ref,),
            evidence_refs=(source_ref, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )

    def from_planning_failure_pattern(
        self,
        *,
        source_ref: LearningReference,
        repeated_failure_count: int,
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
        _require_repeated_pattern(repeated_failure_count, "Planning failure")
        return self.learning.create_candidate(
            source_type=LearningSourceType.PLANNING_FAILURE_PATTERN,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(source_ref,),
            evidence_refs=(source_ref, *evidence_refs),
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
        source_ref = LearningReference(
            kind="research_evidence",
            resource_id=evidence.evidence_id,
            revision=_research_revision(evidence),
            digest=_research_digest(evidence),
        )
        supporting_refs = (
            LearningReference(
                kind="research_item",
                resource_id=evidence.research_item_id,
            ),
            LearningReference(
                kind="research_claim",
                resource_id=evidence.claim_id,
            ),
            LearningReference(
                kind="research_source_observation",
                resource_id=evidence.source_observation_id,
                revision=evidence.source_revision,
                digest=evidence.source_content_digest or evidence.source_snapshot_digest,
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


def _require_repeated_pattern(count: int, name: str) -> None:
    if isinstance(count, bool) or count < 2:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} pattern requires at least two observed failures",
        )


def _research_revision(evidence: object) -> str | None:
    for name in ("source_revision", "source_version", "source_commit", "source_etag"):
        value = getattr(evidence, name)
        if value is not None:
            return str(value)
    return None


def _research_digest(evidence: object) -> str | None:
    for name in ("excerpt_digest", "source_content_digest", "source_snapshot_digest"):
        value = getattr(evidence, name)
        if value is not None:
            return str(value)
    return None
