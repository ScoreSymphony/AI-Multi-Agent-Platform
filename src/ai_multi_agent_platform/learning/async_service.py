"""Awaitable governed-Learning service boundary for runtime persistence hotpaths."""

from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.observability import TelemetryOutcome, TelemetrySeverity
from ai_multi_agent_platform.security import ActorIdentity, RiskClassification

from .async_persistence import (
    LearningPersistenceOffload,
    learning_persistence_offload,
    post_promotion_persistence_offload,
)
from .governance import GovernedObservedLearningService
from .models import (
    FeedbackRecord,
    FeedbackType,
    LearningCandidate,
    LearningCandidateStatus,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
)
from .runtime import PostPromotionEvaluationOutcome, PostPromotionEvaluationRecord
from .service import _promotion_action


async def _await_started_effect[T](operation: asyncio.Task[T]) -> T:
    """Do not surface cancellation until a started cross-domain finalization settles."""

    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        while not operation.done():
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                continue
        failure = operation.exception()
        if failure is not None:
            raise failure from None
        raise


class RuntimeGovernedLearningService(GovernedObservedLearningService):
    """Production Learning service whose synchronous persistence never runs on the event loop."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        persistence_offload = kwargs.pop("persistence_offload", None)
        post_promotion_offload = kwargs.pop("post_promotion_offload", None)
        super().__init__(*args, **kwargs)
        self._persistence_offload = learning_persistence_offload(
            self.repository,
            owner=self,
            requested=persistence_offload,
        )
        self._post_promotion_offload = post_promotion_persistence_offload(
            self.post_promotion_recorder,
            owner=self,
            requested=post_promotion_offload,
        )

    @property
    def persistence_offload(self) -> LearningPersistenceOffload:
        return self._persistence_offload

    @property
    def post_promotion_offload(self) -> LearningPersistenceOffload:
        return self._post_promotion_offload

    async def async_record_feedback(
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
        return await self._persistence_offload.run(
            lambda: self.record_feedback(
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

    async def async_create_candidate(
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
        return await self._persistence_offload.run(
            lambda: self.create_candidate(
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

    async def async_create_from_feedback(
        self,
        feedback: FeedbackRecord,
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
        return await self._persistence_offload.run(
            lambda: self.create_from_feedback(
                feedback,
                problem=problem,
                target=target,
                improvement_type=improvement_type,
                expected_benefit=expected_benefit,
                risk=risk,
                gate_plan=gate_plan,
                creator_ref=creator_ref,
                proposed_change=proposed_change,
                proposed_artifact_ref=proposed_artifact_ref,
                evidence_refs=evidence_refs,
            ),
            message="failed to persist Learning candidate from feedback",
        )

    async def async_record_gate_evidence(
        self,
        learning_candidate_id: str,
        *,
        evaluation_run_ids: tuple[str, ...] = (),
        verification_ids: tuple[str, ...] = (),
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        return await self._persistence_offload.run(
            lambda: self.record_gate_evidence(
                learning_candidate_id,
                evaluation_run_ids=evaluation_run_ids,
                verification_ids=verification_ids,
                expected_revision=expected_revision,
            ),
            message="failed to persist Learning gate evidence",
        )

    async def async_accept(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        return await self._persistence_offload.run(
            lambda: self.accept(
                learning_candidate_id,
                expected_revision=expected_revision,
            ),
            message="failed to accept Learning candidate",
        )

    async def async_reject(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        return await self._persistence_offload.run(
            lambda: self.reject(
                learning_candidate_id,
                expected_revision=expected_revision,
            ),
            message="failed to reject Learning candidate",
        )

    async def async_supersede(
        self,
        learning_candidate_id: str,
        *,
        superseded_by: str,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        return await self._persistence_offload.run(
            lambda: self.supersede(
                learning_candidate_id,
                superseded_by=superseded_by,
                expected_revision=expected_revision,
            ),
            message="failed to supersede Learning candidate",
        )

    async def async_get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate:
        return await self._persistence_offload.run(
            lambda: self.repository.get_candidate(learning_candidate_id, revision),
            message="failed to read Learning candidate",
        )

    async def async_list_candidates(self) -> tuple[LearningCandidate, ...]:
        return await self._persistence_offload.run(
            self.repository.list_candidates,
            message="failed to list Learning candidates",
        )

    async def async_get_feedback(self, feedback_id: str) -> FeedbackRecord:
        return await self._persistence_offload.run(
            lambda: self.repository.get_feedback(feedback_id),
            message="failed to read Learning feedback",
        )

    async def async_list_feedback(self) -> tuple[FeedbackRecord, ...]:
        return await self._persistence_offload.run(
            self.repository.list_feedback,
            message="failed to list Learning feedback",
        )

    async def async_candidate_history(
        self,
        learning_candidate_id: str,
        revision: int,
    ) -> tuple[LearningCandidate, ...]:
        return await self._persistence_offload.run(
            lambda: tuple(
                self.repository.get_candidate(learning_candidate_id, current_revision)
                for current_revision in range(1, revision + 1)
            ),
            message="failed to read Learning candidate history",
        )

    async def async_evaluation_run_project_id(self, evaluation_run_id: str) -> str | None:
        return await self._persistence_offload.run(
            lambda: self.evaluation_run_project_id(evaluation_run_id),
            message="failed to resolve Learning Evaluation project scope",
        )

    async def async_verification_request(self, verification_id: str) -> Any:
        verification = self.quality_gate.verification
        if verification is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Verification service is not configured")
        return await self._persistence_offload.run(
            lambda: verification.get_request(verification_id),
            message="failed to read Learning Verification evidence",
        )

    async def async_target_project_id(self, target: LearningTarget) -> str | None:
        return await self._persistence_offload.run(
            lambda: self.promotion_registry.resolve_project_id(target),
            message="failed to resolve Learning target project scope",
        )

    async def async_list_post_promotion_records(
        self,
        learning_candidate_id: str,
    ) -> tuple[PostPromotionEvaluationRecord, ...]:
        return await self._post_promotion_offload.run(
            lambda: self.post_promotion_recorder.list_for_candidate(learning_candidate_id),
            message="failed to list Learning post-promotion records",
        )

    async def promote(
        self,
        learning_candidate_id: str,
        *,
        actor: ActorIdentity,
        operation: OperationContext,
        approval_id: str | None = None,
        automatic: bool = False,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        current = await self.async_get_candidate(learning_candidate_id)
        self.platform_policy.validate_candidate(current)
        self._require_expected_revision(current, expected_revision)

        if current.status is LearningCandidateStatus.PROMOTED:
            promoted = current
        else:
            if current.status is not LearningCandidateStatus.ACCEPTED:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "only an accepted learning candidate can be promoted",
                )
            if automatic and not self.platform_policy.allows_automatic_promotion(current):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "automatic Learning promotion is disabled by the platform governance floor",
                    details={"platform_policy": self.platform_policy.ref},
                )
            if automatic and not current.gate_plan.automatic_promotion_allowed:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "automatic promotion is disabled by the candidate's versioned gate policy",
                )
            if (
                current.project_id is not None
                and operation.project_id is not None
                and current.project_id != operation.project_id
            ):
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "learning candidate project scope does not match promotion operation",
                )

            await self._persistence_offload.run(
                lambda: self._validate_quality_for_promotion(current),
                message="failed to validate Learning promotion evidence",
            )
            action = _promotion_action(current, actor=actor, operation=operation)
            if self.platform_policy.requires_approval(current):
                await self._require_promotion_approval(
                    action,
                    current,
                    approval_id=approval_id,
                    platform=True,
                )
            if current.risk in current.gate_plan.approval_required_risks:
                await self._require_promotion_approval(
                    action,
                    current,
                    approval_id=approval_id,
                    platform=False,
                )
            await self.authorization_gate.enforce(
                action,
                approval_id=approval_id,
                risk=current.risk,
            )
            adapter = self.promotion_registry.resolve(current.target.resource_type)
            receipt = await adapter.promote(
                current,
                principal_ref=actor.actor_id,
                context=operation,
                actor_type=actor.actor_type.value,
            )
            finalization = asyncio.create_task(self._finalize_promotion(current, receipt))
            promoted = await _await_started_effect(finalization)

        self._emit_candidate("learning.candidate.promoted", promoted, operation=operation)
        await self._run_post_promotion_evaluation(promoted, operation=operation)
        return promoted

    def _validate_quality_for_promotion(self, candidate: LearningCandidate) -> None:
        for run_id in candidate.evaluation_run_ids:
            self.require_evaluation_project_scope(candidate, run_id)
        self.quality_gate.enforce(candidate)

    async def _require_promotion_approval(
        self,
        action: Any,
        candidate: LearningCandidate,
        *,
        approval_id: str | None,
        platform: bool,
    ) -> None:
        resolved = (
            None
            if approval_id is None
            else await self.authorization_gate.runtime_approvals.resolve_valid_for(
                action,
                approval_id=approval_id,
            )
        )
        if resolved is not None:
            return
        if platform:
            reason = (
                "Learning promotion requires Approval under non-bypassable platform "
                f"policy {self.platform_policy.ref}"
            )
            policy_id = f"learning-platform:{self.platform_policy.ref}"
            message = "learning promotion requires platform-governance approval"
            details: dict[str, JsonValue] = {"platform_policy": self.platform_policy.ref}
        else:
            reason = (
                "learning promotion requires explicit human review under "
                f"{candidate.gate_plan.policy_id}@{candidate.gate_plan.policy_version}"
            )
            policy_id = (
                f"learning:{candidate.gate_plan.policy_id}@{candidate.gate_plan.policy_version}"
            )
            message = "learning promotion requires approval"
            details = {}
        approval = await self.authorization_gate.ensure_pending_approval_with_event(
            action,
            reason=reason,
            policy_id=policy_id,
            risk=candidate.risk,
        )
        details.update(
            {
                "approval_id": approval.approval_id,
                "requested_action_digest": action.digest,
            }
        )
        raise ContractError(ErrorCode.FORBIDDEN, message, details=details)

    async def _finalize_promotion(
        self, current: LearningCandidate, receipt: Any
    ) -> LearningCandidate:
        latest = await self.async_get_candidate(current.learning_candidate_id)
        if latest.status is LearningCandidateStatus.PROMOTED:
            return latest
        if latest.revision != current.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate changed while promotion was executing",
            )
        return await self._persistence_offload.run(
            lambda: self._append(
                current,
                status=LearningCandidateStatus.PROMOTED,
                promotion=receipt,
            ),
            message="failed to persist Learning promotion receipt",
        )

    async def _run_post_promotion_evaluation(
        self,
        promoted: LearningCandidate,
        *,
        operation: OperationContext,
    ) -> None:
        promotion = promoted.promotion
        evaluator = self.post_promotion_evaluator
        if (
            promoted.status is not LearningCandidateStatus.PROMOTED
            or promotion is None
            or evaluator is None
        ):
            return
        existing = await self._post_promotion_offload.run(
            lambda: self.post_promotion_recorder.record_for_promotion(
                promoted.learning_candidate_id,
                promotion.new_revision,
            ),
            message="failed to read Learning post-promotion record",
        )
        if existing is not None:
            self._emit(
                "learning.post_promotion_evaluation.reused",
                project_id=promoted.project_id,
                correlation_id=operation.correlation_id,
                attributes={
                    "learning_candidate_id": promoted.learning_candidate_id,
                    "record_id": existing.record_id,
                    "post_promotion_outcome": existing.outcome.value,
                    "target_revision": existing.target_revision,
                },
            )
            return
        try:
            record = await evaluator.evaluate(promoted, operation=operation)
        except Exception as exc:
            record = PostPromotionEvaluationRecord(
                learning_candidate_id=promoted.learning_candidate_id,
                candidate_revision=promoted.revision,
                target_revision=promotion.new_revision,
                outcome=PostPromotionEvaluationOutcome.FAILED,
                details={"error_type": type(exc).__name__},
            )
            await self._post_promotion_offload.run(
                lambda: self.post_promotion_recorder.store(record),
                message="failed to persist Learning post-promotion failure record",
            )
            self._emit(
                "learning.post_promotion_evaluation.failed",
                project_id=promoted.project_id,
                correlation_id=operation.correlation_id,
                outcome=TelemetryOutcome.FAILED,
                severity=TelemetrySeverity.ERROR,
                attributes={
                    "learning_candidate_id": promoted.learning_candidate_id,
                    "candidate_revision": promoted.revision,
                    "record_id": record.record_id,
                    "error_type": type(exc).__name__,
                    "target_revision": record.target_revision,
                },
            )
            return

        await self._post_promotion_offload.run(
            lambda: self.post_promotion_recorder.store(record),
            message="failed to persist Learning post-promotion record",
        )
        if record.outcome is PostPromotionEvaluationOutcome.PASSED:
            telemetry_outcome = TelemetryOutcome.SUCCEEDED
            severity = TelemetrySeverity.INFO
        elif record.outcome is PostPromotionEvaluationOutcome.NOT_CONFIGURED:
            telemetry_outcome = TelemetryOutcome.UNKNOWN
            severity = TelemetrySeverity.INFO
        else:
            telemetry_outcome = TelemetryOutcome.FAILED
            severity = TelemetrySeverity.WARNING
        self._emit(
            "learning.post_promotion_evaluation.completed",
            project_id=promoted.project_id,
            correlation_id=operation.correlation_id,
            outcome=telemetry_outcome,
            severity=severity,
            attributes={
                "learning_candidate_id": promoted.learning_candidate_id,
                "record_id": record.record_id,
                "post_promotion_outcome": record.outcome.value,
                "evaluation_run_ids": list(record.evaluation_run_ids),
                "target_revision": record.target_revision,
            },
        )


__all__ = ["RuntimeGovernedLearningService"]
