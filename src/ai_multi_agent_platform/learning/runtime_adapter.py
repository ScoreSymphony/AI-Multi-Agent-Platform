"""Awaitable adapter for synchronous governed-Learning compatibility services."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.security import ActorIdentity, RiskClassification

from .async_persistence import (
    LearningPersistenceOffload,
    learning_persistence_offload,
    post_promotion_persistence_offload,
)
from .models import (
    FeedbackRecord,
    FeedbackType,
    LearningCandidate,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
)
from .runtime import ObservedLearningService, PostPromotionEvaluationRecord
from .service import LearningService


class LearningRuntimeAdapter:
    """Route synchronous Learning service/repository compatibility calls to owned workers."""

    def __init__(self, service: LearningService) -> None:
        self.service = service
        self._offload = learning_persistence_offload(service.repository, owner=self)
        recorder = getattr(service, "post_promotion_recorder", None)
        self._post_promotion_offload = (
            None if recorder is None else post_promotion_persistence_offload(recorder, owner=self)
        )

    @property
    def offload(self) -> LearningPersistenceOffload:
        return self._offload

    async def record_feedback(
        self,
        *,
        feedback_type: FeedbackType,
        subject: LearningReference,
        creator_ref: str,
        comment: str | None = None,
        target: LearningTarget | None = None,
        project_id: str | None = None,
        provenance: Provenance | None = None,
        feedback_id: str | None = None,
    ) -> tuple[FeedbackRecord, bool]:
        return await self._offload.run(
            lambda: self.service.record_feedback(
                feedback_type=feedback_type,
                subject=subject,
                creator_ref=creator_ref,
                comment=comment,
                target=target,
                project_id=project_id,
                provenance=provenance,
                feedback_id=feedback_id,
            ),
            message="failed to persist Learning feedback",
        )

    async def create_candidate(
        self,
        *,
        source_type: LearningSourceType,
        problem: str,
        target: LearningTarget,
        improvement_type: str,
        expected_benefit: str,
        risk: RiskClassification,
        gate_plan: LearningGatePlan,
        creator_ref: str,
        source_refs: tuple[LearningReference, ...],
        evidence_refs: tuple[LearningReference, ...] = (),
        proposed_change: dict[str, JsonValue] | None = None,
        proposed_artifact_ref: LearningReference | None = None,
        project_id: str | None = None,
        provenance: Provenance | None = None,
        learning_candidate_id: str | None = None,
    ) -> tuple[LearningCandidate, bool]:
        return await self._offload.run(
            lambda: self.service.create_candidate(
                source_type=source_type,
                problem=problem,
                target=target,
                improvement_type=improvement_type,
                expected_benefit=expected_benefit,
                risk=risk,
                gate_plan=gate_plan,
                creator_ref=creator_ref,
                source_refs=source_refs,
                evidence_refs=evidence_refs,
                proposed_change=proposed_change,
                proposed_artifact_ref=proposed_artifact_ref,
                project_id=project_id,
                provenance=provenance,
                learning_candidate_id=learning_candidate_id,
            ),
            message="failed to persist Learning candidate",
        )

    async def create_from_feedback(
        self,
        feedback: FeedbackRecord,
        **kwargs: Any,
    ) -> tuple[LearningCandidate, bool]:
        return await self._offload.run(
            lambda: self.service.create_from_feedback(feedback, **kwargs),
            message="failed to persist Learning candidate from feedback",
        )

    async def record_gate_evidence(
        self,
        learning_candidate_id: str,
        **kwargs: Any,
    ) -> LearningCandidate:
        return await self._offload.run(
            lambda: self.service.record_gate_evidence(learning_candidate_id, **kwargs),
            message="failed to persist Learning gate evidence",
        )

    async def accept(self, learning_candidate_id: str, **kwargs: Any) -> LearningCandidate:
        return await self._offload.run(
            lambda: self.service.accept(learning_candidate_id, **kwargs),
            message="failed to accept Learning candidate",
        )

    async def reject(self, learning_candidate_id: str, **kwargs: Any) -> LearningCandidate:
        return await self._offload.run(
            lambda: self.service.reject(learning_candidate_id, **kwargs),
            message="failed to reject Learning candidate",
        )

    async def supersede(self, learning_candidate_id: str, **kwargs: Any) -> LearningCandidate:
        return await self._offload.run(
            lambda: self.service.supersede(learning_candidate_id, **kwargs),
            message="failed to supersede Learning candidate",
        )

    async def get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate:
        return await self._offload.run(
            lambda: self.service.repository.get_candidate(learning_candidate_id, revision),
            message="failed to read Learning candidate",
        )

    async def list_candidates(self) -> tuple[LearningCandidate, ...]:
        return await self._offload.run(
            self.service.repository.list_candidates,
            message="failed to list Learning candidates",
        )

    async def get_feedback(self, feedback_id: str) -> FeedbackRecord:
        return await self._offload.run(
            lambda: self.service.repository.get_feedback(feedback_id),
            message="failed to read Learning feedback",
        )

    async def list_feedback(self) -> tuple[FeedbackRecord, ...]:
        return await self._offload.run(
            self.service.repository.list_feedback,
            message="failed to list Learning feedback",
        )

    async def candidate_history(
        self,
        learning_candidate_id: str,
        revision: int,
    ) -> tuple[LearningCandidate, ...]:
        return await self._offload.run(
            lambda: tuple(
                self.service.repository.get_candidate(
                    learning_candidate_id,
                    current_revision,
                )
                for current_revision in range(1, revision + 1)
            ),
            message="failed to read Learning candidate history",
        )

    async def evaluation_run_project_id(self, evaluation_run_id: str) -> str | None:
        return await self._offload.run(
            lambda: self.service.evaluation_run_project_id(evaluation_run_id),
            message="failed to resolve Learning Evaluation project scope",
        )

    async def verification_request(self, verification_id: str) -> Any:
        verification = self.service.quality_gate.verification
        if verification is None:
            return None
        return await self._offload.run(
            lambda: verification.get_request(verification_id),
            message="failed to read Learning Verification evidence",
        )

    async def target_project_id(self, target: LearningTarget) -> str | None:
        return await self._offload.run(
            lambda: self.service.promotion_registry.resolve_project_id(target),
            message="failed to resolve Learning target project scope",
        )

    async def promote(
        self,
        learning_candidate_id: str,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
        approval_id: str | None,
        automatic: bool,
        expected_revision: int | None,
    ) -> LearningCandidate:
        # Canonical single-node composition provides RuntimeGovernedLearningService here.
        # Keeping the adapter typed to LearningService preserves the synchronous public
        # compatibility surface for InMemory/offline compositions.
        return await self.service.promote(
            learning_candidate_id,
            actor=actor,
            operation=operation,
            approval_id=approval_id,
            automatic=automatic,
            expected_revision=expected_revision,
        )

    async def list_post_promotion_records(
        self,
        learning_candidate_id: str,
    ) -> tuple[PostPromotionEvaluationRecord, ...]:
        service = self.service
        if not isinstance(service, ObservedLearningService):
            return ()
        assert self._post_promotion_offload is not None
        return await self._post_promotion_offload.run(
            lambda: service.post_promotion_recorder.list_for_candidate(learning_candidate_id),
            message="failed to list Learning post-promotion records",
        )


__all__ = ["LearningRuntimeAdapter"]
