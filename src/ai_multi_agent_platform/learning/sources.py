"""Explicit source adapters for governed Learning Candidate creation (#595/#694)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.research import ResearchService
from ai_multi_agent_platform.research.models import (
    Claim,
    EvidenceRecord,
    ResearchItem,
    SourceObservation,
    SourceRecord,
)
from ai_multi_agent_platform.security import RiskClassification

from .models import (
    LearningCandidate,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
)
from .service import LearningService
from .source_evidence import (
    PlanningFailureEvidenceResolver,
    PlanningFailureSourceRef,
    RunFailureEvidenceResolver,
    RunFailureSourceRef,
    require_operator_source_reference,
)


class LearningSourceBridge:
    """Bind non-authoritative findings to exact canonical evidence before proposing change."""

    def __init__(
        self,
        learning: LearningService,
        *,
        research: ResearchService | None = None,
        run_failures: RunFailureEvidenceResolver | None = None,
        planning_failures: PlanningFailureEvidenceResolver | None = None,
    ) -> None:
        self.learning = learning
        self.research = research
        self.run_failures = run_failures
        self.planning_failures = planning_failures

    async def from_run_failure_pattern(
        self,
        *,
        source_refs: tuple[RunFailureSourceRef, ...],
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
        if self.run_failures is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical Run failure evidence resolver is not configured",
            )
        resolved = tuple(
            [
                await self.run_failures.resolve(reference, project_id=project_id)
                for reference in source_refs
            ]
        )
        bound_sources = _require_repeated_pattern(
            tuple(item.source for item in resolved),
            "Run failure",
        )
        supporting = _unique_references(
            tuple(reference for item in resolved for reference in item.supporting_refs)
        )
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
            evidence_refs=_unique_references((*bound_sources, *supporting, *evidence_refs)),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )

    async def from_planning_failure_pattern(
        self,
        *,
        source_refs: tuple[PlanningFailureSourceRef, ...],
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
        if self.planning_failures is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical Planning failure evidence resolver is not configured",
            )
        resolved = tuple(
            [
                await self.planning_failures.resolve(reference, project_id=project_id)
                for reference in source_refs
            ]
        )
        bound_sources = _require_repeated_pattern(
            tuple(item.source for item in resolved),
            "Planning failure",
        )
        supporting = _unique_references(
            tuple(reference for item in resolved for reference in item.supporting_refs)
        )
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
            evidence_refs=_unique_references((*bound_sources, *supporting, *evidence_refs)),
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
        project_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        if self.research is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Research service is not configured")
        evidence = self.research.repository.get_evidence(evidence_id)
        item = self.research.repository.get_item(evidence.research_item_id)
        claim = self.research.repository.get_claim(evidence.claim_id)
        source = self.research.repository.get_source(evidence.source_id)
        observation = self.research.repository.get_observation(evidence.source_observation_id)
        _require_research_chain(item, claim, source, observation, evidence)
        if project_id is not None and project_id != item.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Learning Candidate project scope does not match canonical Research evidence",
                details={
                    "research_item_id": item.research_item_id,
                    "candidate_project_id": project_id,
                    "evidence_project_id": item.project_id,
                },
            )
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
                kind="research_source",
                resource_id=source.source_id,
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
            evidence_refs=_unique_references((source_ref, *supporting_refs, *evidence_refs)),
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
        proposal_ref = require_operator_source_reference(proposal_ref)
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
            evidence_refs=_unique_references((proposal_ref, *evidence_refs)),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )


def _require_repeated_pattern(
    source_refs: tuple[LearningReference, ...],
    name: str,
) -> tuple[LearningReference, ...]:
    unique = _unique_references(source_refs)
    if len(unique) < 2:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} pattern requires at least two distinct concrete source references",
        )
    return unique


def _unique_references(
    references: tuple[LearningReference, ...],
) -> tuple[LearningReference, ...]:
    unique: list[LearningReference] = []
    seen: set[tuple[str, str, str | None, str | None]] = set()
    for reference in references:
        if reference.key in seen:
            continue
        seen.add(reference.key)
        unique.append(reference)
    return tuple(unique)


def _require_research_chain(
    item: ResearchItem,
    claim: Claim,
    source: SourceRecord,
    observation: SourceObservation,
    evidence: EvidenceRecord,
) -> None:
    expected_item_id = item.research_item_id
    mismatches: list[str] = []
    if claim.research_item_id != expected_item_id:
        mismatches.append("claim.research_item_id")
    if source.research_item_id != expected_item_id:
        mismatches.append("source.research_item_id")
    if observation.research_item_id != expected_item_id:
        mismatches.append("observation.research_item_id")
    if observation.source_id != evidence.source_id:
        mismatches.append("observation.source_id")
    if evidence.claim_id != claim.claim_id:
        mismatches.append("evidence.claim_id")
    if evidence.source_id != source.source_id:
        mismatches.append("evidence.source_id")
    if evidence.source_observation_id != observation.observation_id:
        mismatches.append("evidence.source_observation_id")
    if claim.claim_id not in item.claim_ids:
        mismatches.append("item.claim_ids")
    if source.source_id not in item.source_ids:
        mismatches.append("item.source_ids")
    if evidence.evidence_id not in item.evidence_ids:
        mismatches.append("item.evidence_ids")
    if evidence.evidence_id not in claim.evidence_ids:
        mismatches.append("claim.evidence_ids")
    if observation.observation_id not in source.observation_ids:
        mismatches.append("source.observation_ids")
    if mismatches:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical Research evidence ownership chain is inconsistent",
            details={
                "research_item_id": expected_item_id,
                "evidence_id": evidence.evidence_id,
                "mismatches": mismatches,
            },
        )
