"""Non-bypassable platform governance floor for governed Learning (#595)."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.domain import Provenance
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
    LearningTargetType,
)
from .promotion import PromotionRegistry
from .repository import LearningRepository
from .runtime import (
    ObservedLearningService,
    PostPromotionEvaluationRecorder,
    PostPromotionEvaluator,
)
from .service import LearningQualityGate, _promotion_action


@dataclass(frozen=True, slots=True)
class LearningPlatformPolicy:
    """Deployment-owned safety floor that a Candidate gate plan cannot weaken.

    Candidate gate plans remain versioned evidence/quality policy snapshots. This policy
    is the independent platform minimum. The default is deliberately conservative:
    exact Evaluation/Verification policy references are required when those gates are
    enabled, high/critical changes require Approval, global changes require Approval,
    and automatic promotion is disabled platform-wide.
    """

    policy_id: str = "learning-platform-default"
    policy_version: int = 1
    approval_required_risks: tuple[RiskClassification, ...] = (
        RiskClassification.HIGH,
        RiskClassification.CRITICAL,
    )
    approval_required_for_global_targets: bool = True
    automatic_promotion_enabled: bool = False
    require_versioned_evaluation_refs: bool = True
    require_versioned_verification_refs: bool = True

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("learning platform policy_id must not be blank")
        if self.policy_version < 1:
            raise ValueError("learning platform policy_version must be >= 1")
        if len(set(self.approval_required_risks)) != len(self.approval_required_risks):
            raise ValueError("learning platform Approval risk classes must be unique")

    @property
    def ref(self) -> str:
        return f"{self.policy_id}@{self.policy_version}"

    def validate_candidate(self, candidate: LearningCandidate) -> None:
        self.validate_gate_plan(candidate.gate_plan)

    def validate_gate_plan(self, gate_plan: LearningGatePlan) -> None:
        if (
            self.require_versioned_evaluation_refs
            and gate_plan.require_evaluation
            and not gate_plan.evaluation_suite_refs
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Learning platform policy requires explicit versioned Evaluation suite refs",
                details={"platform_policy": self.ref},
            )
        if (
            self.require_versioned_verification_refs
            and gate_plan.require_verification
            and not gate_plan.verification_policy_refs
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Learning platform policy requires explicit versioned Verification policy refs",
                details={"platform_policy": self.ref},
            )

    def requires_approval(self, candidate: LearningCandidate) -> bool:
        if candidate.risk in self.approval_required_risks:
            return True
        return self.approval_required_for_global_targets and candidate.project_id is None

    def allows_automatic_promotion(self, candidate: LearningCandidate) -> bool:
        return (
            self.automatic_promotion_enabled
            and candidate.gate_plan.automatic_promotion_allowed
            and candidate.project_id is not None
            and candidate.risk is RiskClassification.STANDARD
        )


class GovernedObservedLearningService(ObservedLearningService):
    """Production-shaped Learning service with an independent platform policy floor."""

    def __init__(
        self,
        repository: LearningRepository,
        *,
        quality_gate: LearningQualityGate,
        promotion_registry: PromotionRegistry,
        authorization_gate: AuthorizationGate,
        platform_policy: LearningPlatformPolicy | None = None,
        telemetry=None,
        post_promotion_evaluator: PostPromotionEvaluator | None = None,
        post_promotion_recorder: PostPromotionEvaluationRecorder | None = None,
    ) -> None:
        super().__init__(
            repository,
            quality_gate=quality_gate,
            promotion_registry=promotion_registry,
            authorization_gate=authorization_gate,
            telemetry=telemetry,
            post_promotion_evaluator=post_promotion_evaluator,
            post_promotion_recorder=post_promotion_recorder,
        )
        self.platform_policy = platform_policy or LearningPlatformPolicy()

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
        self.platform_policy.validate_gate_plan(gate_plan)
        return super().create_candidate(
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

    def accept(
        self,
        learning_candidate_id: str,
        *,
        expected_revision: int | None = None,
    ) -> LearningCandidate:
        self.platform_policy.validate_candidate(self.repository.get_candidate(learning_candidate_id))
        return super().accept(
            learning_candidate_id,
            expected_revision=expected_revision,
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
        self.platform_policy.validate_candidate(current)
        if current.status is LearningCandidateStatus.PROMOTED:
            return await super().promote(
                learning_candidate_id,
                actor=actor,
                operation=operation,
                approval_id=approval_id,
                automatic=automatic,
                expected_revision=expected_revision,
            )
        if automatic and not self.platform_policy.allows_automatic_promotion(current):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "automatic Learning promotion is disabled by the platform governance floor",
                details={"platform_policy": self.platform_policy.ref},
            )
        if (
            current.status is LearningCandidateStatus.ACCEPTED
            and self.platform_policy.requires_approval(current)
        ):
            action = _promotion_action(current, actor=actor, operation=operation)
            if approval_id is None or not self.authorization_gate.approvals.valid_for(
                approval_id, action
            ):
                approval = await self.authorization_gate.ensure_pending_approval_with_event(
                    action,
                    reason=(
                        "Learning promotion requires Approval under non-bypassable platform "
                        f"policy {self.platform_policy.ref}"
                    ),
                    policy_id=f"learning-platform:{self.platform_policy.ref}",
                    risk=current.risk,
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "learning promotion requires platform-governance approval",
                    details={
                        "approval_id": approval.approval_id,
                        "requested_action_digest": action.digest,
                        "platform_policy": self.platform_policy.ref,
                    },
                )
        return await super().promote(
            learning_candidate_id,
            actor=actor,
            operation=operation,
            approval_id=approval_id,
            automatic=automatic,
            expected_revision=expected_revision,
        )


__all__ = ["GovernedObservedLearningService", "LearningPlatformPolicy"]
