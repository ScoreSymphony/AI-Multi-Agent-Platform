"""Runtime-safe source adapters for governed Learning Candidate creation."""

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
from .runtime_adapter import LearningRuntimeAdapter
from .service import LearningService
from .source_evidence import (
    PlanningFailureEvidenceResolver,
    PlanningFailureSourceRef,
    RunFailureEvidenceResolver,
    RunFailureSourceRef,
)
from .sources import LearningSourceBridge, _require_repeated_pattern, _unique_references


class RuntimeLearningSourceBridge(LearningSourceBridge):
    """Keep async source resolution free of synchronous Learning persistence."""

    def __init__(
        self,
        learning: LearningService,
        *,
        research: ResearchService | None = None,
        run_failures: RunFailureEvidenceResolver | None = None,
        planning_failures: PlanningFailureEvidenceResolver | None = None,
    ) -> None:
        super().__init__(
            learning,
            research=research,
            run_failures=run_failures,
            planning_failures=planning_failures,
        )
        self.runtime = LearningRuntimeAdapter(learning)

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
        return await self.runtime.create_candidate(
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
        return await self.runtime.create_candidate(
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


__all__ = ["RuntimeLearningSourceBridge"]
