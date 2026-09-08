"""Observable governed-Learning runtime and optional post-promotion evaluation (#595)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.domain import Provenance, new_id
from ai_multi_agent_platform.evaluation import ConfigurationSnapshot, EvaluationService
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)
from ai_multi_agent_platform.security import ActorIdentity, AuthorizationGate, RiskClassification

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
from .promotion import PromotionRegistry
from .repository import LearningRepository
from .service import LearningQualityGate, LearningService


class PostPromotionEvaluationOutcome(StrEnum):
    PASSED = "passed"
    REGRESSION = "regression"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class PostPromotionEvaluationRecord:
    """Derived post-promotion evidence; the promoted owner revision remains authoritative."""

    learning_candidate_id: str
    candidate_revision: int
    target_revision: int
    outcome: PostPromotionEvaluationOutcome
    evaluation_run_ids: tuple[str, ...] = ()
    details: Mapping[str, JsonValue] = field(default_factory=dict)
    record_id: str = field(default_factory=lambda: new_id("learning_post_evaluation"))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.candidate_revision < 1 or self.target_revision < 1:
            raise ValueError("post-promotion revisions must be positive")
        if len(self.evaluation_run_ids) != len(set(self.evaluation_run_ids)):
            raise ValueError("post-promotion evaluation run IDs must be unique")
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


class PostPromotionEvaluationRecorder(Protocol):
    def store(self, record: PostPromotionEvaluationRecord) -> None: ...

    def list_for_candidate(
        self,
        learning_candidate_id: str,
    ) -> tuple[PostPromotionEvaluationRecord, ...]: ...


class InMemoryPostPromotionEvaluationRecorder:
    def __init__(self) -> None:
        self._records: list[PostPromotionEvaluationRecord] = []

    def store(self, record: PostPromotionEvaluationRecord) -> None:
        if any(existing.record_id == record.record_id for existing in self._records):
            return
        self._records.append(record)

    def list_for_candidate(
        self,
        learning_candidate_id: str,
    ) -> tuple[PostPromotionEvaluationRecord, ...]:
        return tuple(
            record
            for record in self._records
            if record.learning_candidate_id == learning_candidate_id
        )


class PostPromotionEvaluator(Protocol):
    async def evaluate(
        self,
        candidate: LearningCandidate,
        *,
        operation: OperationContext,
    ) -> PostPromotionEvaluationRecord: ...


class EvaluationPostPromotionEvaluator:
    """Run configured #19 suites against the newly promoted canonical owner revision."""

    def __init__(
        self,
        evaluation: EvaluationService,
        *,
        snapshot_factory: Callable[[LearningCandidate], ConfigurationSnapshot],
        suite_refs: tuple[str, ...] = (),
    ) -> None:
        self._evaluation = evaluation
        self._snapshot_factory = snapshot_factory
        self._suite_refs = suite_refs

    async def evaluate(
        self,
        candidate: LearningCandidate,
        *,
        operation: OperationContext,
    ) -> PostPromotionEvaluationRecord:
        del operation
        if candidate.promotion is None:
            raise ValueError("post-promotion Evaluation requires a PromotionReceipt")
        suite_refs = self._suite_refs or candidate.gate_plan.evaluation_suite_refs
        if not suite_refs:
            return PostPromotionEvaluationRecord(
                learning_candidate_id=candidate.learning_candidate_id,
                candidate_revision=candidate.revision,
                target_revision=candidate.promotion.new_revision,
                outcome=PostPromotionEvaluationOutcome.PASSED,
                details={"reason": "no post-promotion suites configured"},
            )

        snapshot = self._snapshot_factory(candidate)
        run_ids: list[str] = []
        regression_count = 0
        failed_results = 0
        for suite_ref in suite_refs:
            baseline_run_id = self._baseline_for_suite(candidate, suite_ref)
            summary = await self._evaluation.run_suite(
                suite_ref=suite_ref,
                snapshot=snapshot,
                baseline_run_id=baseline_run_id,
            )
            run_ids.append(summary.run.run_id)
            if summary.comparison is not None:
                regression_count += len(summary.comparison.regressions)
            failed_results += sum(
                1 for result in summary.results if result.outcome.value != "passed"
            )

        outcome = (
            PostPromotionEvaluationOutcome.REGRESSION
            if regression_count > 0
            else PostPromotionEvaluationOutcome.FAILED
            if failed_results > 0
            else PostPromotionEvaluationOutcome.PASSED
        )
        return PostPromotionEvaluationRecord(
            learning_candidate_id=candidate.learning_candidate_id,
            candidate_revision=candidate.revision,
            target_revision=candidate.promotion.new_revision,
            outcome=outcome,
            evaluation_run_ids=tuple(run_ids),
            details={
                "regression_count": regression_count,
                "failed_result_count": failed_results,
            },
        )

    def _baseline_for_suite(self, candidate: LearningCandidate, suite_ref: str) -> str | None:
        for run_id in reversed(candidate.evaluation_run_ids):
            detail = self._evaluation.get_run_detail(run_id)
            if f"{detail.run.suite_id}@{detail.run.suite_version}" == suite_ref:
                return run_id
        return None


class ObservedLearningService(LearningService):
    """LearningService with redacted telemetry and optional post-promotion Evaluation."""

    def __init__(
        self,
        repository: LearningRepository,
        *,
        quality_gate: LearningQualityGate,
        promotion_registry: PromotionRegistry,
        authorization_gate: AuthorizationGate,
        telemetry: Telemetry | None = None,
        post_promotion_evaluator: PostPromotionEvaluator | None = None,
        post_promotion_recorder: PostPromotionEvaluationRecorder | None = None,
    ) -> None:
        super().__init__(
            repository,
            quality_gate=quality_gate,
            promotion_registry=promotion_registry,
            authorization_gate=authorization_gate,
        )
        self.telemetry = telemetry
        self.post_promotion_evaluator = post_promotion_evaluator
        self.post_promotion_recorder = (
            post_promotion_recorder or InMemoryPostPromotionEvaluationRecorder()
        )

    def record_feedback(
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
        record, created = super().record_feedback(
            feedback_type=feedback_type,
            subject=subject,
            creator_ref=creator_ref,
            comment=comment,
            target=target,
            project_id=project_id,
            provenance=provenance,
            feedback_id=feedback_id,
        )
        self._emit(
            "learning.feedback.recorded",
            project_id=record.project_id,
            attributes={
                "feedback_id": record.feedback_id,
                "feedback_type": record.feedback_type.value,
                "created": created,
                "subject_kind": record.subject.kind,
            },
        )
        return record, created

    def create_candidate(
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
        candidate, created = super().create_candidate(
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
        )
        self._emit_candidate(
            "learning.candidate.created" if created else "learning.candidate.linked",
            candidate,
        )
        return candidate, created

    def record_gate_evidence(
        self,
        learning_candidate_id: str,
        *,
        evaluation_run_ids: tuple[str, ...] = (),
        verification_ids: tuple[str, ...] = (),
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        candidate = super().record_gate_evidence(
            learning_candidate_id,
            evaluation_run_ids=evaluation_run_ids,
            verification_ids=verification_ids,
            expected_revision=expected_revision,
        )
        self._emit_candidate("learning.candidate.evidence_recorded", candidate)
        return candidate

    def accept(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        candidate = super().accept(
            learning_candidate_id,
            expected_revision=expected_revision,
        )
        self._emit_candidate("learning.candidate.accepted", candidate)
        return candidate

    def reject(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        candidate = super().reject(
            learning_candidate_id,
            expected_revision=expected_revision,
        )
        self._emit_candidate("learning.candidate.rejected", candidate)
        return candidate

    def supersede(
        self,
        learning_candidate_id: str,
        *,
        superseded_by: str,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        candidate = super().supersede(
            learning_candidate_id,
            superseded_by=superseded_by,
            expected_revision=expected_revision,
        )
        self._emit_candidate("learning.candidate.superseded", candidate)
        return candidate

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
        promoted = await super().promote(
            learning_candidate_id,
            actor=actor,
            operation=operation,
            approval_id=approval_id,
            automatic=automatic,
            expected_revision=expected_revision,
        )
        self._emit_candidate("learning.candidate.promoted", promoted, operation=operation)
        if (
            promoted.status is LearningCandidateStatus.PROMOTED
            and self.post_promotion_evaluator is not None
        ):
            try:
                record = await self.post_promotion_evaluator.evaluate(
                    promoted,
                    operation=operation,
                )
            except Exception as exc:
                self._emit(
                    "learning.post_promotion_evaluation.failed",
                    project_id=promoted.project_id,
                    correlation_id=operation.correlation_id,
                    outcome=TelemetryOutcome.FAILED,
                    severity=TelemetrySeverity.ERROR,
                    attributes={
                        "learning_candidate_id": promoted.learning_candidate_id,
                        "candidate_revision": promoted.revision,
                        "error_type": type(exc).__name__,
                    },
                )
            else:
                self.post_promotion_recorder.store(record)
                self._emit(
                    "learning.post_promotion_evaluation.completed",
                    project_id=promoted.project_id,
                    correlation_id=operation.correlation_id,
                    outcome=(
                        TelemetryOutcome.SUCCEEDED
                        if record.outcome is PostPromotionEvaluationOutcome.PASSED
                        else TelemetryOutcome.FAILED
                    ),
                    severity=(
                        TelemetrySeverity.INFO
                        if record.outcome is PostPromotionEvaluationOutcome.PASSED
                        else TelemetrySeverity.WARNING
                    ),
                    attributes={
                        "learning_candidate_id": promoted.learning_candidate_id,
                        "record_id": record.record_id,
                        "post_promotion_outcome": record.outcome.value,
                        "evaluation_run_ids": list(record.evaluation_run_ids),
                        "target_revision": record.target_revision,
                    },
                )
        return promoted

    def _emit_candidate(
        self,
        event_name: str,
        candidate: LearningCandidate,
        *,
        operation: OperationContext | None = None,
    ) -> None:
        self._emit(
            event_name,
            project_id=candidate.project_id,
            correlation_id=None if operation is None else operation.correlation_id,
            attributes={
                "learning_candidate_id": candidate.learning_candidate_id,
                "candidate_revision": candidate.revision,
                "candidate_status": candidate.status.value,
                "candidate_digest": candidate.content_digest,
                "source_type": candidate.source_type.value,
                "target_type": candidate.target.resource_type.value,
                "target_id": candidate.target.resource_id,
                "target_revision": candidate.target.revision,
                "risk": candidate.risk.value,
                "gate_policy": (
                    f"{candidate.gate_plan.policy_id}@{candidate.gate_plan.policy_version}"
                ),
            },
        )

    def _emit(
        self,
        event_name: str,
        *,
        project_id: str | None,
        attributes: dict[str, JsonValue],
        correlation_id: str | None = None,
        outcome: TelemetryOutcome = TelemetryOutcome.SUCCEEDED,
        severity: TelemetrySeverity = TelemetrySeverity.INFO,
    ) -> None:
        if self.telemetry is None:
            return
        context = TelemetryContext(
            project_id=project_id,
            correlation_id=correlation_id,
        )
        self.telemetry.log(
            severity=severity,
            component=FailureComponent.ORCHESTRATION,
            event_name=event_name,
            context=context,
            outcome=outcome,
            attributes=attributes,
        )
        self.telemetry.timeline(
            event_name=event_name,
            component=FailureComponent.ORCHESTRATION,
            context=context,
            outcome=outcome,
            attributes=attributes,
        )
