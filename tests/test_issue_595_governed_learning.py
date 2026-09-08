from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.evaluation import EvaluationOutcome, EvaluationRunStatus
from ai_multi_agent_platform.learning import (
    AgentPromotionAdapter,
    FeedbackType,
    LearningCandidateStatus,
    LearningGatePlan,
    LearningQualityGate,
    LearningReference,
    LearningService,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    PromotionRegistry,
    RoutingProfilePromotionAdapter,
    SkillPromotionAdapter,
    SQLiteLearningRepository,
)
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileService,
    RoutingRequirements,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    RiskClassification,
)
from ai_multi_agent_platform.skills import (
    InMemorySkillRepository,
    SkillContent,
    SkillProfile,
    SkillService,
)
from ai_multi_agent_platform.verification import VerificationOutcome

OWNER = OwnerRef(type="user", id="issue-595-owner")
OPERATOR = ActorIdentity("user:operator", ActorType.HUMAN)
REVIEWER = ActorIdentity("user:reviewer", ActorType.HUMAN)


class _EvaluationStub:
    def __init__(self) -> None:
        self._details: dict[str, SimpleNamespace] = {}

    def add(
        self,
        run_id: str,
        *,
        outcome: EvaluationOutcome = EvaluationOutcome.PASSED,
        regressions: bool = False,
    ) -> SimpleNamespace:
        result = SimpleNamespace(
            result_id=f"{run_id}-result",
            outcome=outcome,
        )
        findings = (
            (
                SimpleNamespace(
                    current_result_id=result.result_id,
                    case_id="case-learning",
                ),
            )
            if regressions
            else ()
        )
        detail = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version=1,
                status=EvaluationRunStatus.COMPLETED,
            ),
            results=(result,),
            comparison=SimpleNamespace(regressions=findings),
        )
        self._details[run_id] = detail
        return detail

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        return self._details[run_id]


class _VerificationStub:
    def __init__(self) -> None:
        self._requests: dict[str, SimpleNamespace] = {}
        self._results: dict[str, SimpleNamespace] = {}

    def add(
        self,
        verification_id: str,
        *,
        outcome: VerificationOutcome,
        project_id: str | None = None,
    ) -> tuple[SimpleNamespace, SimpleNamespace]:
        request = SimpleNamespace(
            verification_id=verification_id,
            policy_id="learning-verification",
            policy_version=1,
            project_id=project_id,
        )
        result = SimpleNamespace(
            verification_result_id=f"{verification_id}-result",
            outcome=outcome,
            subject=SimpleNamespace(revision="1", digest=f"digest-{verification_id}"),
        )
        self._requests[verification_id] = request
        self._results[verification_id] = result
        return request, result

    def get_request(self, verification_id: str) -> SimpleNamespace:
        return self._requests[verification_id]

    def result_for(self, verification_id: str) -> SimpleNamespace | None:
        return self._results.get(verification_id)


class _FailPromotionAppendRepository(SQLiteLearningRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.fail_once = True

    def append_candidate(self, candidate, *, expected_revision: int):
        if self.fail_once and candidate.status is LearningCandidateStatus.PROMOTED:
            self.fail_once = False
            raise RuntimeError("simulated process crash after owner promotion")
        return super().append_candidate(candidate, expected_revision=expected_revision)


def _operation(*, project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-595",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _gate_plan(
    *,
    require_verification: bool = False,
    approval_required_risks: tuple[RiskClassification, ...] = (
        RiskClassification.HIGH,
        RiskClassification.CRITICAL,
    ),
) -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="learning-policy",
        policy_version=1,
        require_evaluation=True,
        require_verification=require_verification,
        evaluation_suite_refs=("learning-suite@1",),
        verification_policy_refs=("learning-verification@1",) if require_verification else (),
        approval_required_risks=approval_required_risks,
    )


def _authorization_gate(*, allow_modify: bool = True) -> AuthorizationGate:
    operator_actions = (
        frozenset({AuthorizationAction.MODIFY})
        if allow_modify
        else frozenset({AuthorizationAction.READ})
    )
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=OPERATOR.actor_id,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=operator_actions,
            ),
            LocalPrincipalPolicy(
                principal_ref=REVIEWER.actor_id,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
            ),
        )
    )
    return AuthorizationGate(provider)


def _service(
    repository,
    evaluation: _EvaluationStub,
    *,
    verification: _VerificationStub | None = None,
    adapters=(),
    allow_modify: bool = True,
    authorization_gate: AuthorizationGate | None = None,
) -> LearningService:
    return LearningService(
        repository,
        quality_gate=LearningQualityGate(
            evaluation=evaluation,
            verification=verification,
        ),
        promotion_registry=PromotionRegistry(tuple(adapters)),
        authorization_gate=authorization_gate or _authorization_gate(allow_modify=allow_modify),
    )


def _target(
    resource_type: LearningTargetType = LearningTargetType.AGENT,
    *,
    resource_id: str | None = None,
    revision: int = 1,
) -> LearningTarget:
    prefix = {
        LearningTargetType.AGENT: "agent",
        LearningTargetType.SKILL: "skill",
        LearningTargetType.MODEL_ROUTING_PROFILE: "model_routing_profile",
    }.get(resource_type, "resource")
    return LearningTarget(
        resource_type=resource_type,
        resource_id=resource_id or new_id(prefix),
        revision=revision,
    )


def _propose_and_accept(
    learning: LearningService,
    *,
    target: LearningTarget,
    proposed_change: dict[str, object],
    risk: RiskClassification = RiskClassification.STANDARD,
    run_id: str = "eval-pass",
) -> object:
    candidate, created = learning.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="Observed behavior should be improved.",
        target=target,
        improvement_type="owner_revision",
        expected_benefit="Improve deterministic task quality.",
        risk=risk,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        source_refs=(LearningReference(kind="operator_proposal", resource_id="proposal-595"),),
        proposed_change=proposed_change,
    )
    assert created is True
    evaluating = learning.record_gate_evidence(
        candidate.learning_candidate_id,
        evaluation_run_ids=(run_id,),
        expected_revision=candidate.revision,
    )
    accepted = learning.accept(
        candidate.learning_candidate_id,
        expected_revision=evaluating.revision,
    )
    assert accepted.status is LearningCandidateStatus.ACCEPTED
    return accepted


def _agent_service() -> tuple[AgentService, object]:
    service = AgentService(InMemoryAgentRepository())
    revision = service.create_agent(
        AgentProfile(
            name="Issue 595 Agent",
            role="worker",
            description="Original agent behavior.",
            instructions=AgentInstructions(
                role=InstructionSource(content="Use the original method.", version="1")
            ),
        ),
        owner_ref=OWNER,
    )
    return service, revision


def _skill_service() -> tuple[SkillService, object]:
    service = SkillService(InMemorySkillRepository())
    revision = service.create_skill(
        SkillProfile(
            name="Issue 595 Skill",
            purpose_categories=("test",),
            description="Original skill behavior.",
            content=SkillContent(content="original method"),
        ),
        owner_ref=OWNER,
    )
    return service, revision


def test_user_correction_creates_candidate_bound_to_exact_feedback(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)
    target = _target()
    feedback, created = learning.record_feedback(
        feedback_type=FeedbackType.CORRECTION,
        subject=LearningReference(
            kind="run_result",
            resource_id=new_id("result"),
            revision="1",
            digest="sha256:result-evidence",
        ),
        creator_ref="user:corrector",
        comment="Use the bounded method instead.",
        target=target,
    )
    assert created is True

    candidate, candidate_created = learning.create_from_feedback(
        feedback,
        problem="The result used the wrong method.",
        target=target,
        improvement_type="instruction_correction",
        expected_benefit="Use the corrected method on equivalent tasks.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "Use the bounded method."},
    )

    assert candidate_created is True
    assert candidate.source_type is LearningSourceType.USER_FEEDBACK
    assert candidate.target == target
    assert candidate.source_refs[0].resource_id == feedback.feedback_id
    assert candidate.source_refs[0].digest == feedback.content_digest
    assert (
        learning.repository.get_feedback(feedback.feedback_id).content_digest
        == feedback.content_digest
    )


def test_verification_finding_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    verification = _VerificationStub()
    verification.add("verify-595", outcome=VerificationOutcome.NEEDS_CHANGES)
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        verification=verification,
    )

    candidate, created = learning.create_from_verification(
        "verify-595",
        problem="Verification requires a safer method.",
        target=_target(),
        improvement_type="verification_fix",
        expected_benefit="Pass the bounded verification policy.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(require_verification=True),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "Apply the verified method."},
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.VERIFICATION
    assert candidate.source_refs[0].kind == "verification_result"
    assert {item.kind for item in candidate.evidence_refs} == {
        "verification",
        "verification_result",
    }


def test_evaluation_regression_creates_candidate(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    detail = evaluation.add("eval-regression", regressions=True)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)

    candidate, created = learning.create_from_evaluation_regression(
        "eval-regression",
        problem="The current revision regressed a deterministic case.",
        target=_target(),
        improvement_type="regression_fix",
        expected_benefit="Restore the regression fixture.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "Restore deterministic behavior."},
    )

    assert created is True
    assert candidate.source_type is LearningSourceType.EVALUATION
    assert candidate.source_refs[0].resource_id == detail.run.run_id
    assert any(item.kind == "evaluation_result" for item in candidate.evidence_refs)


def test_duplicate_candidate_deduplicates_and_links_evidence(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    repository = SQLiteLearningRepository(tmp_path / "learning.db")
    learning = _service(repository, evaluation)
    target = _target()
    common = dict(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="Repeated deterministic failure pattern.",
        target=target,
        improvement_type="method_revision",
        expected_benefit="Reduce repeated failures.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "Use method B."},
    )

    first, first_created = learning.create_candidate(
        **common,
        source_refs=(LearningReference(kind="run_failure", resource_id="failure-a"),),
    )
    second, second_created = learning.create_candidate(
        **common,
        source_refs=(LearningReference(kind="run_failure", resource_id="failure-b"),),
    )

    assert first_created is True
    assert second_created is False
    assert second.learning_candidate_id == first.learning_candidate_id
    assert second.revision == first.revision + 1
    assert {item.resource_id for item in second.source_refs} == {"failure-a", "failure-b"}
    assert repository.get_candidate(first.learning_candidate_id, 1).source_refs == first.source_refs


def test_agent_revision_proposal_evaluation_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    agents, original = _agent_service()
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.AGENT,
            resource_id=original.agent_id,
            revision=original.revision,
        ),
        proposed_change={"role_instruction": "Use the evaluated bounded method."},
    )

    promoted = asyncio.run(
        learning.promote(
            accepted.learning_candidate_id,
            actor=OPERATOR,
            operation=_operation(),
            expected_revision=accepted.revision,
        )
    )
    latest = agents.get_agent_revision(original.agent_id)

    assert promoted.status is LearningCandidateStatus.PROMOTED
    assert latest.revision == original.revision + 1
    assert latest.profile.instructions.role.content == "Use the evaluated bounded method."
    assert latest.provenance is not None
    assert latest.provenance.source == "learning_candidate"
    assert latest.provenance.details["learning_candidate_id"] == accepted.learning_candidate_id


def test_skill_revision_proposal_evaluation_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    skills, original = _skill_service()
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(SkillPromotionAdapter(skills),),
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.SKILL,
            resource_id=original.skill_id,
            revision=original.revision,
        ),
        proposed_change={"content": "evaluated replacement method"},
    )

    promoted = asyncio.run(
        learning.promote(
            accepted.learning_candidate_id,
            actor=OPERATOR,
            operation=_operation(),
            expected_revision=accepted.revision,
        )
    )
    latest = skills.get_skill_revision(original.skill_id)

    assert promoted.status is LearningCandidateStatus.PROMOTED
    assert latest.revision == original.revision + 1
    assert latest.profile.content.content == "evaluated replacement method"
    assert latest.provenance is not None
    assert latest.provenance.source == "learning_candidate"


def test_routing_profile_proposal_evaluation_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    routing_repository = JsonModelRoutingProfileRepository(tmp_path / "routing.json")
    routing = ModelRoutingProfileService(routing_repository)
    original = asyncio.run(
        routing.create_profile(
            name="Issue 595 routing",
            description="Original routing description.",
            policy=ModelRoutingProfilePolicy(
                requirements=RoutingRequirements(tool_calling=True),
                preferred_model_ids=("model-original",),
            ),
            owner_ref=OWNER,
            principal_ref=OPERATOR.actor_id,
            context=_operation(),
        )
    )
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(RoutingProfilePromotionAdapter(routing),),
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.MODEL_ROUTING_PROFILE,
            resource_id=original.profile_id,
            revision=original.revision,
        ),
        proposed_change={"description": "Evaluated routing description."},
    )

    promoted = asyncio.run(
        learning.promote(
            accepted.learning_candidate_id,
            actor=OPERATOR,
            operation=_operation(),
            expected_revision=accepted.revision,
        )
    )
    definition = routing_repository.get_definition(original.profile_id)
    latest = routing_repository.get_revision(definition.current_ref)

    assert promoted.status is LearningCandidateStatus.PROMOTED
    assert latest.revision == original.revision + 1
    assert latest.description == "Evaluated routing description."
    assert latest.provenance is not None
    assert latest.provenance.source == "learning_candidate"


def test_stale_target_revision_rejects_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    agents, original = _agent_service()
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.AGENT,
            resource_id=original.agent_id,
            revision=original.revision,
        ),
        proposed_change={"description": "Candidate revision."},
    )
    agents.update_agent(
        original.agent_id,
        replace(original.profile, description="Newer human revision."),
        expected_revision=original.revision,
        provenance=Provenance(source="human_edit", actor_ref="user:human"),
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(
            learning.promote(
                accepted.learning_candidate_id,
                actor=OPERATOR,
                operation=_operation(),
            )
        )

    assert error.value.code is ErrorCode.CONFLICT
    assert agents.get_agent_revision(original.agent_id).revision == original.revision + 1
    assert (
        learning.get_candidate(accepted.learning_candidate_id).status
        is LearningCandidateStatus.ACCEPTED
    )


def test_unauthorized_promotion_is_denied(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    agents, original = _agent_service()
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
        allow_modify=False,
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.AGENT,
            resource_id=original.agent_id,
            revision=original.revision,
        ),
        proposed_change={"description": "Unauthorized candidate revision."},
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(
            learning.promote(
                accepted.learning_candidate_id,
                actor=OPERATOR,
                operation=_operation(),
            )
        )

    assert error.value.code is ErrorCode.FORBIDDEN
    assert agents.get_agent_revision(original.agent_id).revision == original.revision


def test_high_risk_promotion_requires_exact_approval(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    agents, original = _agent_service()
    authorization = _authorization_gate()
    learning = _service(
        SQLiteLearningRepository(tmp_path / "learning.db"),
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
        authorization_gate=authorization,
    )
    accepted = _propose_and_accept(
        learning,
        target=_target(
            LearningTargetType.AGENT,
            resource_id=original.agent_id,
            revision=original.revision,
        ),
        proposed_change={"description": "High-risk evaluated revision."},
        risk=RiskClassification.HIGH,
    )

    with pytest.raises(ContractError) as error:
        asyncio.run(
            learning.promote(
                accepted.learning_candidate_id,
                actor=OPERATOR,
                operation=_operation(),
            )
        )
    assert error.value.code is ErrorCode.FORBIDDEN
    approval = authorization.approvals.all()[-1]
    assert approval.requested_action_digest == error.value.details["requested_action_digest"]
    asyncio.run(
        authorization.decide_approval(
            approval.approval_id,
            approver=REVIEWER,
            approve=True,
            operation=_operation(),
        )
    )

    promoted = asyncio.run(
        learning.promote(
            accepted.learning_candidate_id,
            actor=OPERATOR,
            operation=_operation(),
            approval_id=approval.approval_id,
        )
    )

    assert promoted.status is LearningCandidateStatus.PROMOTED
    assert agents.get_agent_revision(original.agent_id).revision == original.revision + 1


def test_failed_evaluation_blocks_acceptance_and_promotion(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-fail", outcome=EvaluationOutcome.FAILED)
    learning = _service(SQLiteLearningRepository(tmp_path / "learning.db"), evaluation)
    candidate, _ = learning.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="Candidate must prove the proposed method.",
        target=_target(),
        improvement_type="method_revision",
        expected_benefit="Improve quality.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        source_refs=(LearningReference(kind="operator_proposal", resource_id="proposal-fail"),),
        proposed_change={"description": "Unproven method."},
    )
    evaluating = learning.record_gate_evidence(
        candidate.learning_candidate_id,
        evaluation_run_ids=("eval-fail",),
    )

    with pytest.raises(ContractError) as error:
        learning.accept(
            candidate.learning_candidate_id,
            expected_revision=evaluating.revision,
        )

    assert error.value.code is ErrorCode.CONFLICT
    assert (
        learning.get_candidate(candidate.learning_candidate_id).status
        is LearningCandidateStatus.EVALUATING
    )


def test_restart_during_promotion_does_not_duplicate_owner_revision(tmp_path: Path) -> None:
    evaluation = _EvaluationStub()
    evaluation.add("eval-pass")
    agents, original = _agent_service()
    path = tmp_path / "learning.db"
    failing_repository = _FailPromotionAppendRepository(path)
    first_service = _service(
        failing_repository,
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
    )
    accepted = _propose_and_accept(
        first_service,
        target=_target(
            LearningTargetType.AGENT,
            resource_id=original.agent_id,
            revision=original.revision,
        ),
        proposed_change={"description": "Crash-safe evaluated revision."},
    )

    with pytest.raises(RuntimeError, match="simulated process crash"):
        asyncio.run(
            first_service.promote(
                accepted.learning_candidate_id,
                actor=OPERATOR,
                operation=_operation(),
            )
        )
    assert agents.get_agent_revision(original.agent_id).revision == original.revision + 1
    assert (
        first_service.get_candidate(accepted.learning_candidate_id).status
        is LearningCandidateStatus.ACCEPTED
    )

    restarted = _service(
        SQLiteLearningRepository(path),
        evaluation,
        adapters=(AgentPromotionAdapter(agents),),
    )
    promoted = asyncio.run(
        restarted.promote(
            accepted.learning_candidate_id,
            actor=OPERATOR,
            operation=_operation(),
        )
    )

    assert promoted.status is LearningCandidateStatus.PROMOTED
    assert promoted.promotion is not None
    assert promoted.promotion.already_applied is True
    assert agents.get_agent_revision(original.agent_id).revision == original.revision + 1


def test_historical_feedback_verification_and_evaluation_evidence_are_not_mutated(
    tmp_path: Path,
) -> None:
    evaluation = _EvaluationStub()
    regression_detail = evaluation.add("eval-source", regressions=True)
    verification = _VerificationStub()
    verification_request, verification_result = verification.add(
        "verify-source",
        outcome=VerificationOutcome.FAIL,
    )
    repository = SQLiteLearningRepository(tmp_path / "learning.db")
    learning = _service(repository, evaluation, verification=verification)
    target = _target()
    feedback, _ = learning.record_feedback(
        feedback_type=FeedbackType.FINDING,
        subject=LearningReference(
            kind="run_result",
            resource_id=new_id("result"),
            revision="1",
            digest="sha256:immutable",
        ),
        creator_ref="user:reviewer",
        comment="Historical source evidence.",
        target=target,
    )
    feedback_digest = feedback.content_digest
    regression_ids = tuple(
        finding.current_result_id for finding in regression_detail.comparison.regressions
    )
    verification_outcome = verification_result.outcome

    from_feedback, _ = learning.create_from_feedback(
        feedback,
        problem="Feedback-backed candidate.",
        target=target,
        improvement_type="feedback_fix",
        expected_benefit="Preserve source while proposing a fix.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "feedback proposal"},
    )
    from_verification, _ = learning.create_from_verification(
        verification_request.verification_id,
        problem="Verification-backed candidate.",
        target=_target(resource_id=new_id("agent")),
        improvement_type="verification_fix",
        expected_benefit="Preserve verification evidence.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "verification proposal"},
    )
    from_evaluation, _ = learning.create_from_evaluation_regression(
        regression_detail.run.run_id,
        problem="Evaluation-backed candidate.",
        target=_target(resource_id=new_id("agent")),
        improvement_type="regression_fix",
        expected_benefit="Preserve regression evidence.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref=OPERATOR.actor_id,
        proposed_change={"description": "evaluation proposal"},
    )
    learning.reject(from_feedback.learning_candidate_id)
    learning.reject(from_verification.learning_candidate_id)
    learning.reject(from_evaluation.learning_candidate_id)

    assert repository.get_feedback(feedback.feedback_id).content_digest == feedback_digest
    assert verification.result_for("verify-source") is verification_result
    assert verification_result.outcome is verification_outcome
    assert evaluation.get_run_detail("eval-source") is regression_detail
    assert (
        tuple(finding.current_result_id for finding in regression_detail.comparison.regressions)
        == regression_ids
    )
