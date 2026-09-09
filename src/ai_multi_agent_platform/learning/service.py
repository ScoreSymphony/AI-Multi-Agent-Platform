"""Governed feedback, learning-candidate and improvement-promotion workflow (#595)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    EvaluationRunStatus,
    EvaluationService,
)
from ai_multi_agent_platform.evaluation.product import parse_agent_evaluation_target
from ai_multi_agent_platform.security import (
    ActorIdentity,
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
)
from ai_multi_agent_platform.verification import VerificationOutcome, VerificationService

from .models import (
    FeedbackRecord,
    FeedbackType,
    LearningCandidate,
    LearningCandidateStatus,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    new_feedback_id,
    new_learning_candidate_id,
    reference_to_dict,
    target_to_dict,
)
from .promotion import PromotionRegistry
from .repository import LearningRepository

_TERMINAL_STATUSES = frozenset(
    {
        LearningCandidateStatus.REJECTED,
        LearningCandidateStatus.SUPERSEDED,
        LearningCandidateStatus.PROMOTED,
    }
)
_IDENTITY_BOUND_TARGET_TYPES = frozenset(
    {
        LearningTargetType.AGENT,
        LearningTargetType.SKILL,
        LearningTargetType.MODEL_ROUTING_PROFILE,
    }
)


class LearningQualityGate:
    """Read-only bridge to canonical Evaluation and Verification evidence."""

    def __init__(
        self,
        *,
        evaluation: EvaluationService | None = None,
        verification: VerificationService | None = None,
    ) -> None:
        self.evaluation = evaluation
        self.verification = verification

    def enforce(self, candidate: LearningCandidate) -> None:
        if candidate.gate_plan.require_evaluation:
            self._enforce_evaluation(candidate)
        if candidate.gate_plan.require_verification:
            self._enforce_verification(candidate)

    def _enforce_evaluation(self, candidate: LearningCandidate) -> None:
        if self.evaluation is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "learning candidate requires Evaluation but no Evaluation service is configured",
            )
        if not candidate.evaluation_run_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate is missing required Evaluation evidence",
            )
        allowed_suites = set(candidate.gate_plan.evaluation_suite_refs)
        for run_id in candidate.evaluation_run_ids:
            detail = self.evaluation.get_run_detail(run_id)
            run = detail.run
            if run.status is not EvaluationRunStatus.COMPLETED:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Evaluation run is not completed",
                    details={"evaluation_run_id": run_id, "status": run.status.value},
                )
            _require_evaluation_manifest(detail, run_id)
            _require_evaluation_target_binding(candidate, detail, run_id)
            suite_ref = f"{run.suite_id}@{run.suite_version}"
            if allowed_suites and suite_ref not in allowed_suites:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "learning candidate Evaluation run uses an unplanned suite",
                    details={"evaluation_run_id": run_id, "suite_ref": suite_ref},
                )
            if not detail.results:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Evaluation run has no result evidence",
                    details={"evaluation_run_id": run_id},
                )
            failures = [
                result.result_id
                for result in detail.results
                if result.outcome is not EvaluationOutcome.PASSED
            ]
            if failures:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate failed its Evaluation gate",
                    details={
                        "evaluation_run_id": run_id,
                        "failing_result_ids": cast(JsonValue, failures),
                    },
                )
            if candidate.gate_plan.require_regression_free:
                comparison = detail.comparison
                if comparison is None:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "learning candidate requires a persisted Evaluation comparison",
                        details={"evaluation_run_id": run_id},
                    )
                _require_comparison_identity(run, comparison, run_id)
                if comparison.regressions:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "learning candidate has Evaluation regressions",
                        details={
                            "evaluation_run_id": run_id,
                            "regression_count": len(comparison.regressions),
                        },
                    )

    def _enforce_verification(self, candidate: LearningCandidate) -> None:
        if self.verification is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "learning candidate requires Verification but no Verification "
                "service is configured",
            )
        if not candidate.verification_ids:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate is missing required Verification evidence",
            )
        allowed_policies = set(candidate.gate_plan.verification_policy_refs)
        for verification_id in candidate.verification_ids:
            request = self.verification.get_request(verification_id)
            if request.project_id != candidate.project_id:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "learning candidate project scope does not match Verification evidence",
                    details={
                        "verification_id": verification_id,
                        "candidate_project_id": candidate.project_id,
                        "verification_project_id": request.project_id,
                    },
                )
            policy_ref = f"{request.policy_id}@{request.policy_version}"
            if allowed_policies and policy_ref not in allowed_policies:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "learning candidate Verification uses an unplanned policy",
                    details={
                        "verification_id": verification_id,
                        "verification_policy_ref": policy_ref,
                    },
                )
            result = self.verification.result_for(verification_id)
            if result is None:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate Verification has no completed result",
                    details={"verification_id": verification_id},
                )
            _require_verification_target_binding(
                candidate.target,
                candidate.proposed_artifact_ref,
                request,
                result,
                verification_id,
            )
            if result.outcome is not VerificationOutcome.PASS:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "learning candidate failed its Verification gate",
                    details={
                        "verification_id": verification_id,
                        "outcome": result.outcome.value,
                    },
                )


class LearningService:
    """Canonical coordinator. Candidate evidence never becomes mutation authority by itself."""

    def __init__(
        self,
        repository: LearningRepository,
        *,
        quality_gate: LearningQualityGate,
        promotion_registry: PromotionRegistry,
        authorization_gate: AuthorizationGate,
    ) -> None:
        self.repository = repository
        self.quality_gate = quality_gate
        self.promotion_registry = promotion_registry
        self.authorization_gate = authorization_gate

    def evaluation_run_project_id(self, evaluation_run_id: str) -> str | None:
        """Resolve the canonical project scope of one persisted Evaluation run's targets."""

        evaluation = self.quality_gate.evaluation
        if evaluation is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Evaluation service is not configured")
        detail = evaluation.get_run_detail(evaluation_run_id)
        suite_ref = f"{detail.run.suite_id}@{detail.run.suite_version}"
        suite = evaluation.get_suite(suite_ref)
        snapshot_targets = {
            (reference.ref_id, reference.version)
            for reference in detail.run.snapshot.references
            if reference.kind == "agent"
        }
        project_ids: set[str | None] = set()
        found_target = False
        for case in suite.cases:
            try:
                target = parse_agent_evaluation_target(case)
            except ValueError as exc:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Evaluation run suite contains an invalid canonical target",
                    details={"evaluation_run_id": evaluation_run_id, "suite_ref": suite_ref},
                ) from exc
            if target is None:
                continue
            found_target = True
            identity = (target.agent_id, str(target.agent_revision))
            if identity not in snapshot_targets:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "Evaluation run snapshot does not pin its declared Agent target",
                    details={
                        "evaluation_run_id": evaluation_run_id,
                        "agent_id": target.agent_id,
                        "agent_revision": target.agent_revision,
                    },
                )
            learning_target = LearningTarget(
                resource_type=LearningTargetType.AGENT,
                resource_id=target.agent_id,
                revision=target.agent_revision,
            )
            if not self.promotion_registry.supports(learning_target.resource_type):
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "Learning cannot resolve Evaluation Agent target scope",
                    details={"evaluation_run_id": evaluation_run_id},
                )
            project_ids.add(self.promotion_registry.resolve_project_id(learning_target))
        if not found_target:
            return None
        if len(project_ids) != 1:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Evaluation evidence spans multiple project scopes",
                details={"evaluation_run_id": evaluation_run_id},
            )
        return next(iter(project_ids))

    def require_evaluation_project_scope(
        self,
        candidate: LearningCandidate,
        evaluation_run_id: str,
    ) -> None:
        evaluation_project_id = self.evaluation_run_project_id(evaluation_run_id)
        if evaluation_project_id is not None:
            _require_matching_evaluation_project_scope(
                candidate.project_id,
                evaluation_project_id,
                evaluation_run_id,
            )
            return
        if (
            candidate.target.resource_type in _IDENTITY_BOUND_TARGET_TYPES
            and self.promotion_registry.supports(candidate.target.resource_type)
        ):
            _require_matching_evaluation_project_scope(
                candidate.project_id,
                self.promotion_registry.resolve_project_id(candidate.target),
                evaluation_run_id,
            )
            return
        _require_matching_evaluation_project_scope(
            candidate.project_id,
            evaluation_project_id,
            evaluation_run_id,
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
        record = FeedbackRecord(
            feedback_id=feedback_id or new_feedback_id(),
            feedback_type=feedback_type,
            subject=subject,
            creator_ref=creator_ref,
            comment=comment,
            target=target,
            project_id=project_id,
            provenance=provenance,
        )
        return self.repository.create_feedback(record, dedupe_key=_feedback_dedupe_key(record))

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
        candidate = LearningCandidate(
            learning_candidate_id=learning_candidate_id or new_learning_candidate_id(),
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
            proposed_change=proposed_change or {},
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
            provenance=provenance,
        )
        existing = self.repository.find_candidate_by_dedupe_key(candidate.dedupe_key)
        if existing is not None:
            return self._link_duplicate(existing, candidate), False
        return self.repository.create_candidate(candidate, dedupe_key=candidate.dedupe_key)

    def create_from_feedback(
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
        stored = self.repository.get_feedback(feedback.feedback_id)
        if stored.content_digest != feedback.content_digest:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "feedback source does not match immutable stored evidence",
            )
        source_ref = LearningReference(
            kind="feedback",
            resource_id=stored.feedback_id,
            digest=stored.content_digest,
        )
        return self.create_candidate(
            source_type=LearningSourceType.USER_FEEDBACK,
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
            project_id=stored.project_id,
        )

    def create_from_verification(
        self,
        verification_id: str,
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
        verification = self.quality_gate.verification
        if verification is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Verification service is not configured")
        request = verification.get_request(verification_id)
        result = verification.result_for(verification_id)
        if result is None:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Verification finding is not completed",
                details={"verification_id": verification_id},
            )
        if result.outcome not in {
            VerificationOutcome.FAIL,
            VerificationOutcome.NEEDS_CHANGES,
            VerificationOutcome.INCONCLUSIVE,
        }:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "passing Verification does not represent a failure learning source",
            )
        _require_verification_target_binding(
            target,
            proposed_artifact_ref,
            request,
            result,
            verification_id,
        )
        result_ref = LearningReference(
            kind="verification_result",
            resource_id=result.verification_result_id,
            revision=result.subject.revision,
            digest=result.subject.digest,
        )
        request_ref = LearningReference(
            kind="verification",
            resource_id=request.verification_id,
            revision=f"{request.policy_id}@{request.policy_version}",
        )
        return self.create_candidate(
            source_type=LearningSourceType.VERIFICATION,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(result_ref,),
            evidence_refs=(request_ref, result_ref, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=request.project_id,
        )

    def create_from_evaluation_regression(
        self,
        evaluation_run_id: str,
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
        evaluation = self.quality_gate.evaluation
        if evaluation is None:
            raise ContractError(ErrorCode.UNAVAILABLE, "Evaluation service is not configured")
        detail = evaluation.get_run_detail(evaluation_run_id)
        if detail.run.status is not EvaluationRunStatus.COMPLETED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Evaluation regression source is not completed",
                details={"evaluation_run_id": evaluation_run_id},
            )
        _require_evaluation_manifest(detail, evaluation_run_id)
        _require_evaluation_target_binding_for_target(target, detail, evaluation_run_id)
        if detail.comparison is None or not detail.comparison.regressions:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Evaluation run does not contain a regression finding",
            )
        _require_comparison_identity(detail.run, detail.comparison, evaluation_run_id)
        project_id = (
            self.promotion_registry.resolve_project_id(target)
            if self.promotion_registry.supports(target.resource_type)
            else None
        )
        evaluation_project_id = self.evaluation_run_project_id(evaluation_run_id)
        if (
            evaluation_project_id is not None
            or target.resource_type not in _IDENTITY_BOUND_TARGET_TYPES
        ):
            _require_matching_evaluation_project_scope(
                project_id,
                evaluation_project_id,
                evaluation_run_id,
            )
        run_ref = LearningReference(
            kind="evaluation_run",
            resource_id=detail.run.run_id,
            revision=f"{detail.run.suite_id}@{detail.run.suite_version}",
        )
        finding_refs = tuple(
            LearningReference(
                kind="evaluation_result",
                resource_id=finding.current_result_id,
                revision=finding.case_id,
            )
            for finding in detail.comparison.regressions
        )
        return self.create_candidate(
            source_type=LearningSourceType.EVALUATION,
            problem=problem,
            target=target,
            improvement_type=improvement_type,
            expected_benefit=expected_benefit,
            risk=risk,
            gate_plan=gate_plan,
            creator_ref=creator_ref,
            source_refs=(run_ref,),
            evidence_refs=(run_ref, *finding_refs, *evidence_refs),
            proposed_change=proposed_change,
            proposed_artifact_ref=proposed_artifact_ref,
            project_id=project_id,
        )

    def record_gate_evidence(
        self,
        learning_candidate_id: str,
        *,
        evaluation_run_ids: tuple[str, ...] = (),
        verification_ids: tuple[str, ...] = (),
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        current = self.repository.get_candidate(learning_candidate_id)
        self._require_expected_revision(current, expected_revision)
        if (
            current.status in _TERMINAL_STATUSES
            or current.status is LearningCandidateStatus.ACCEPTED
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                f"cannot add gate evidence to candidate in {current.status.value} status",
            )
        evidence = list(current.evidence_refs)
        eval_ids = list(current.evaluation_run_ids)
        verification_refs = list(current.verification_ids)
        if evaluation_run_ids:
            evaluation = self.quality_gate.evaluation
            if evaluation is None:
                raise ContractError(ErrorCode.UNAVAILABLE, "Evaluation service is not configured")
            for run_id in evaluation_run_ids:
                detail = evaluation.get_run_detail(run_id)
                _require_evaluation_target_binding(current, detail, run_id)
                self.require_evaluation_project_scope(current, run_id)
                if run_id not in eval_ids:
                    eval_ids.append(run_id)
                _append_reference(
                    evidence,
                    LearningReference(
                        kind="evaluation_run",
                        resource_id=run_id,
                        revision=f"{detail.run.suite_id}@{detail.run.suite_version}",
                    ),
                )
        if verification_ids:
            verification = self.quality_gate.verification
            if verification is None:
                raise ContractError(ErrorCode.UNAVAILABLE, "Verification service is not configured")
            for verification_id in verification_ids:
                request = verification.get_request(verification_id)
                if request.project_id != current.project_id:
                    raise ContractError(
                        ErrorCode.FORBIDDEN,
                        "learning candidate project scope does not match Verification evidence",
                        details={
                            "verification_id": verification_id,
                            "candidate_project_id": current.project_id,
                            "verification_project_id": request.project_id,
                        },
                    )
                result = verification.result_for(verification_id)
                _require_verification_target_binding(
                    current.target,
                    current.proposed_artifact_ref,
                    request,
                    result,
                    verification_id,
                )
                if verification_id not in verification_refs:
                    verification_refs.append(verification_id)
                _append_reference(
                    evidence,
                    LearningReference(
                        kind="verification",
                        resource_id=verification_id,
                        revision=f"{request.policy_id}@{request.policy_version}",
                    ),
                )
                if result is not None:
                    _append_reference(
                        evidence,
                        LearningReference(
                            kind="verification_result",
                            resource_id=result.verification_result_id,
                            revision=result.subject.revision,
                            digest=result.subject.digest,
                        ),
                    )
        status = (
            LearningCandidateStatus.EVALUATING
            if current.status is LearningCandidateStatus.PROPOSED
            else current.status
        )
        return self._append(
            current,
            status=status,
            evidence_refs=tuple(evidence),
            evaluation_run_ids=tuple(eval_ids),
            verification_ids=tuple(verification_refs),
        )

    def accept(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        current = self.repository.get_candidate(learning_candidate_id)
        self._require_expected_revision(current, expected_revision)
        if current.status not in {
            LearningCandidateStatus.PROPOSED,
            LearningCandidateStatus.EVALUATING,
        }:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"cannot accept candidate in {current.status.value} status",
            )
        for run_id in current.evaluation_run_ids:
            self.require_evaluation_project_scope(current, run_id)
        self.quality_gate.enforce(current)
        return self._append(current, status=LearningCandidateStatus.ACCEPTED)

    def reject(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        current = self.repository.get_candidate(learning_candidate_id)
        self._require_expected_revision(current, expected_revision)
        if current.status in _TERMINAL_STATUSES:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"cannot reject candidate in {current.status.value} status",
            )
        return self._append(current, status=LearningCandidateStatus.REJECTED)

    def supersede(
        self,
        learning_candidate_id: str,
        *,
        superseded_by: str,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        current = self.repository.get_candidate(learning_candidate_id)
        self._require_expected_revision(current, expected_revision)
        replacement = self.repository.get_candidate(superseded_by)
        if current.status in _TERMINAL_STATUSES:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"cannot supersede candidate in {current.status.value} status",
            )
        if current.learning_candidate_id == replacement.learning_candidate_id:
            raise ContractError(ErrorCode.INVALID_REQUEST, "candidate cannot supersede itself")
        if current.target.resource_type is not replacement.target.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "replacement learning candidate must target the same owner domain",
            )
        return self._append(
            current,
            status=LearningCandidateStatus.SUPERSEDED,
            superseded_by=replacement.learning_candidate_id,
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
        current = self.repository.get_candidate(learning_candidate_id)
        self._require_expected_revision(current, expected_revision)
        if current.status is LearningCandidateStatus.PROMOTED:
            return current
        if current.status is not LearningCandidateStatus.ACCEPTED:
            raise ContractError(
                ErrorCode.CONFLICT,
                "only an accepted learning candidate can be promoted",
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
        for run_id in current.evaluation_run_ids:
            self.require_evaluation_project_scope(current, run_id)
        self.quality_gate.enforce(current)
        adapter = self.promotion_registry.resolve(current.target.resource_type)
        action = _promotion_action(current, actor=actor, operation=operation)
        if current.risk in current.gate_plan.approval_required_risks:
            if approval_id is None or not self.authorization_gate.approvals.valid_for(
                approval_id, action
            ):
                approval = await self.authorization_gate.ensure_pending_approval_with_event(
                    action,
                    reason=(
                        "learning promotion requires explicit human review under "
                        f"{current.gate_plan.policy_id}@{current.gate_plan.policy_version}"
                    ),
                    policy_id=(
                        f"learning:{current.gate_plan.policy_id}@{current.gate_plan.policy_version}"
                    ),
                    risk=current.risk,
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "learning promotion requires approval",
                    details={
                        "approval_id": approval.approval_id,
                        "requested_action_digest": action.digest,
                    },
                )
        await self.authorization_gate.enforce(action, approval_id=approval_id, risk=current.risk)
        receipt = await adapter.promote(
            current,
            principal_ref=actor.actor_id,
            context=operation,
            actor_type=actor.actor_type.value,
        )
        latest = self.repository.get_candidate(learning_candidate_id)
        if latest.status is LearningCandidateStatus.PROMOTED:
            return latest
        if latest.revision != current.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate changed while promotion was executing",
            )
        return self._append(current, status=LearningCandidateStatus.PROMOTED, promotion=receipt)

    def get_candidate(
        self,
        learning_candidate_id: str,
        revision: int | None = None,
    ) -> LearningCandidate:
        return self.repository.get_candidate(learning_candidate_id, revision)

    def list_candidates(self) -> tuple[LearningCandidate, ...]:
        return self.repository.list_candidates()

    def _link_duplicate(
        self,
        current: LearningCandidate,
        incoming: LearningCandidate,
    ) -> LearningCandidate:
        source_refs = list(current.source_refs)
        evidence_refs = list(current.evidence_refs)
        for reference in incoming.source_refs:
            _append_reference(source_refs, reference)
        for reference in incoming.evidence_refs:
            _append_reference(evidence_refs, reference)
        if (
            tuple(source_refs) == current.source_refs
            and tuple(evidence_refs) == current.evidence_refs
        ):
            return current
        return self._append(
            current,
            source_refs=tuple(source_refs),
            evidence_refs=tuple(evidence_refs),
        )

    def _append(self, current: LearningCandidate, **changes: Any) -> LearningCandidate:
        updated = replace(
            current,
            revision=current.revision + 1,
            updated_at=datetime.now(UTC),
            **changes,
        )
        return self.repository.append_candidate(updated, expected_revision=current.revision)

    @staticmethod
    def _require_expected_revision(
        current: LearningCandidate,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is not None and current.revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "learning candidate changed after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.revision,
                },
            )


def _promotion_action(
    candidate: LearningCandidate,
    *,
    actor: ActorIdentity,
    operation: OperationContext,
) -> ProposedAction:
    resource_type = {
        LearningTargetType.AGENT: ResourceType.AGENT,
        LearningTargetType.SKILL: ResourceType.GENERIC,
        LearningTargetType.MODEL_ROUTING_PROFILE: ResourceType.MODEL_ROUTING_PROFILE,
    }.get(candidate.target.resource_type, ResourceType.GENERIC)
    return ProposedAction(
        AuthorizationContext(
            actor=actor,
            action=AuthorizationAction.MODIFY,
            resource_type=resource_type,
            resource_id=candidate.target.resource_id,
            operation=operation,
            side_effect="learning_promotion",
            security_labels=("learning_candidate", candidate.risk.value),
            trust_context={
                "learning_candidate_id": candidate.learning_candidate_id,
                "learning_candidate_revision": candidate.revision,
                "learning_policy_id": candidate.gate_plan.policy_id,
                "learning_policy_version": candidate.gate_plan.policy_version,
            },
        ),
        payload={
            "learning_candidate_id": candidate.learning_candidate_id,
            "learning_candidate_revision": candidate.revision,
            "learning_candidate_digest": candidate.content_digest,
            "target_revision": candidate.target.revision,
            "target_type": candidate.target.resource_type.value,
        },
        payload_ref=(
            f"{candidate.learning_candidate_id}@r{candidate.revision}#{candidate.content_digest}"
        ),
    )


def _feedback_dedupe_key(record: FeedbackRecord) -> str:
    payload = {
        "feedback_type": record.feedback_type.value,
        "subject": reference_to_dict(record.subject),
        "creator_ref": record.creator_ref,
        "comment": record.comment,
        "target": None if record.target is None else target_to_dict(record.target),
        "project_id": record.project_id,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_evaluation_manifest(detail: Any, evaluation_run_id: str) -> None:
    manifest = getattr(detail, "manifest", None)
    if manifest is None:
        raise ContractError(
            ErrorCode.CONFLICT,
            "learning Evaluation evidence is missing its persisted EvalManifest",
            details={"evaluation_run_id": evaluation_run_id},
        )
    run = detail.run
    if (
        manifest.evaluation_run_id != run.run_id
        or manifest.suite_id != run.suite_id
        or str(manifest.suite_version) != str(run.suite_version)
    ):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "learning Evaluation manifest identity does not match the run",
            details={"evaluation_run_id": evaluation_run_id},
        )


def _require_evaluation_target_binding(
    candidate: LearningCandidate,
    detail: Any,
    evaluation_run_id: str,
) -> None:
    _require_evaluation_target_binding_for_target(candidate.target, detail, evaluation_run_id)
    if candidate.proposed_artifact_ref is not None:
        _require_evaluation_manifest(detail, evaluation_run_id)
        if not _manifest_contains_learning_reference(
            detail.manifest, candidate.proposed_artifact_ref
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Evaluation manifest does not pin the proposed candidate artifact",
                details={"evaluation_run_id": evaluation_run_id},
            )


def _require_evaluation_target_binding_for_target(
    target: LearningTarget,
    detail: Any,
    evaluation_run_id: str,
) -> None:
    if target.resource_type not in _IDENTITY_BOUND_TARGET_TYPES:
        return
    _require_evaluation_manifest(detail, evaluation_run_id)
    expected_ids = {target.resource_id}
    if target.resource_type is LearningTargetType.MODEL_ROUTING_PROFILE:
        expected_ids.add(f"{target.resource_id}@r{target.revision}")
    snapshot_match = _has_exact_target_reference(
        detail.run.snapshot.references,
        target,
        expected_ids,
    )
    manifest_match = _has_exact_target_reference(
        detail.manifest.configuration_references,
        target,
        expected_ids,
    )
    if not snapshot_match or not manifest_match:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Evaluation evidence does not match the exact learning target identity",
            details={
                "evaluation_run_id": evaluation_run_id,
                "target_type": target.resource_type.value,
                "target_id": target.resource_id,
                "target_revision": target.revision,
            },
        )


def _has_exact_target_reference(
    references: Any,
    target: LearningTarget,
    expected_ids: set[str],
) -> bool:
    expected_revision = str(target.revision)
    for reference in references:
        if reference.kind != target.resource_type.value or reference.ref_id not in expected_ids:
            continue
        if str(getattr(reference, "version", "")) == expected_revision:
            return True
    return False


def _manifest_contains_learning_reference(manifest: Any, expected: LearningReference) -> bool:
    groups = (
        manifest.configuration_references,
        manifest.skill_bundles,
        manifest.context_bundles,
        manifest.research_evidence,
        manifest.fixture_sources,
        manifest.verification_policies,
        manifest.dependencies,
        manifest.contract_versions,
        (() if manifest.workspace is None else (manifest.workspace,)),
    )
    for group in groups:
        for reference in group:
            if reference.kind != expected.kind or reference.ref_id != expected.resource_id:
                continue
            if expected.revision is not None and expected.revision not in {
                getattr(reference, "revision", None),
                getattr(reference, "version", None),
            }:
                continue
            if (
                expected.digest is not None
                and getattr(reference, "digest", None) != expected.digest
            ):
                continue
            return True
    return False


def _require_comparison_identity(run: Any, comparison: Any, evaluation_run_id: str) -> None:
    baseline_run_id = getattr(run, "baseline_run_id", None)
    if baseline_run_id is None:
        raise ContractError(
            ErrorCode.CONFLICT,
            "learning regression evidence has no persisted baseline run",
            details={"evaluation_run_id": evaluation_run_id},
        )
    if comparison.current_run_id != run.run_id or comparison.baseline_run_id != baseline_run_id:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Evaluation comparison does not match the current/baseline run references",
            details={
                "evaluation_run_id": evaluation_run_id,
                "baseline_run_id": baseline_run_id,
            },
        )


def _require_verification_target_binding(
    target: LearningTarget,
    proposed_artifact_ref: LearningReference | None,
    request: Any,
    result: Any | None,
    verification_id: str,
) -> None:
    if target.resource_type not in _IDENTITY_BOUND_TARGET_TYPES:
        return
    subject = request.subject
    if result is not None and result.subject != subject:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Verification result does not match the exact requested subject",
            details={"verification_id": verification_id},
        )
    if proposed_artifact_ref is not None and _verification_subject_matches_reference(
        subject, proposed_artifact_ref
    ):
        return
    producer = getattr(request, "producer", None)
    if (
        target.resource_type is LearningTargetType.AGENT
        and producer is not None
        and getattr(producer, "agent_id", None) == target.resource_id
        and getattr(producer, "agent_revision", None) == target.revision
    ):
        return
    raise ContractError(
        ErrorCode.CONTRACT_VIOLATION,
        "Verification evidence is not bound to the exact learning target or candidate artifact",
        details={
            "verification_id": verification_id,
            "target_type": target.resource_type.value,
            "target_id": target.resource_id,
            "target_revision": target.revision,
        },
    )


def _verification_subject_matches_reference(subject: Any, reference: LearningReference) -> bool:
    if subject.subject_type != reference.kind or subject.subject_id != reference.resource_id:
        return False
    if reference.revision is not None and subject.revision != reference.revision:
        return False
    if reference.digest is not None and subject.digest != reference.digest:
        return False
    return reference.revision is not None or reference.digest is not None


def _require_matching_evaluation_project_scope(
    candidate_project_id: str | None,
    evaluation_project_id: str | None,
    evaluation_run_id: str,
) -> None:
    if candidate_project_id != evaluation_project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "learning candidate project scope does not match Evaluation evidence",
            details={
                "evaluation_run_id": evaluation_run_id,
                "candidate_project_id": candidate_project_id,
                "evaluation_project_id": evaluation_project_id,
            },
        )


def _append_reference(values: list[LearningReference], reference: LearningReference) -> None:
    if all(item.key != reference.key for item in values):
        values.append(reference)
