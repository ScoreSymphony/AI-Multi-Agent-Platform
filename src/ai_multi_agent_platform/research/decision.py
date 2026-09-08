"""Governed Research -> Decision Record provenance bridge for issue #589.

Decision Records (#598) remain the authority for architectural/project decisions. Research only
projects exact, current and optionally verified Research references into that existing domain.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.decisions import (
    DecisionAlternative,
    DecisionOutcome,
    DecisionRecord,
    DecisionRecordView,
    DecisionReference,
    DecisionService,
)

from .service import ResearchService


@dataclass(frozen=True, slots=True)
class ResearchDecisionReferences:
    """Exact Research provenance suitable for one immutable DecisionRecord."""

    research_item: DecisionReference
    claims: tuple[DecisionReference, ...]
    evidence: tuple[DecisionReference, ...]
    verifications: tuple[DecisionReference, ...] = ()

    @property
    def evidence_refs(self) -> tuple[DecisionReference, ...]:
        return (*self.claims, *self.evidence, *self.verifications)


class ResearchDecisionBridge:
    """Create #598 Decision Records from decision-ready canonical Research.

    The bridge deliberately calls ``ResearchService.build_action_context`` before producing
    references. Unsupported/disputed Claims, stale/unavailable Evidence or missing required #86
    Verification therefore fail closed before a DecisionRecord is created. The bridge does not
    approve the decision, activate a downstream resource or bypass #15 authorization.
    """

    def __init__(self, research: ResearchService, decisions: DecisionService) -> None:
        self.research = research
        self.decisions = decisions

    def references(
        self,
        research_item_id: str,
        *,
        require_verification: bool = True,
    ) -> ResearchDecisionReferences:
        action = self.research.build_action_context(
            research_item_id,
            require_verification=require_verification,
        )
        item = self.research.repository.get_item(research_item_id)
        claims = tuple(
            self.research.repository.get_claim(claim_id) for claim_id in action.claim_ids
        )
        evidence = tuple(
            self.research.repository.get_evidence(evidence_id)
            for evidence_id in action.evidence_ids
        )
        return ResearchDecisionReferences(
            research_item=DecisionReference(
                kind="research-item",
                resource_id=item.research_item_id,
                revision=item.revision,
                digest=item.digest,
            ),
            claims=tuple(
                DecisionReference(
                    kind="research-claim",
                    resource_id=claim.claim_id,
                    revision=claim.revision,
                    digest=claim.digest,
                )
                for claim in claims
            ),
            evidence=tuple(
                DecisionReference(
                    kind="research-evidence",
                    resource_id=value.evidence_id,
                    revision=value.source_observation_id,
                    digest=value.digest,
                    metadata={
                        "source_id": value.source_id,
                        "source_observation_id": value.source_observation_id,
                        "relation": value.relation.value,
                    },
                )
                for value in evidence
            ),
            verifications=tuple(
                DecisionReference(kind="verification", resource_id=verification_id)
                for verification_id in action.verification_ids
            ),
        )

    def create_decision(
        self,
        research_item_id: str,
        *,
        title: str,
        subject: str,
        category: str,
        scope_type: str,
        question: str,
        alternatives: tuple[DecisionAlternative, ...],
        outcome: DecisionOutcome,
        rationale: str,
        actor_ref: str,
        scope_id: str | None = None,
        reviewer_refs: tuple[str, ...] = (),
        approval_ref: DecisionReference | None = None,
        evaluation_refs: tuple[DecisionReference, ...] = (),
        finding_refs: tuple[DecisionReference, ...] = (),
        cost_resource_refs: tuple[DecisionReference, ...] = (),
        require_verification: bool = True,
    ) -> DecisionRecordView:
        references = self.references(
            research_item_id,
            require_verification=require_verification,
        )
        return self.decisions.create(
            DecisionRecord(
                title=title,
                subject=subject,
                category=category,
                scope_type=scope_type,
                scope_id=scope_id,
                question=question,
                alternatives=alternatives,
                outcome=outcome,
                rationale=rationale,
                actor_ref=actor_ref,
                subject_ref=references.research_item,
                evidence_refs=references.evidence_refs,
                evaluation_refs=evaluation_refs,
                finding_refs=finding_refs,
                cost_resource_refs=cost_resource_refs,
                reviewer_refs=reviewer_refs,
                approval_ref=approval_ref,
            )
        )


__all__ = ["ResearchDecisionBridge", "ResearchDecisionReferences"]
