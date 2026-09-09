from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.evaluation import EvaluationOutcome, EvaluationRunStatus
from ai_multi_agent_platform.learning import (
    LearningGatePlan,
    LearningQualityGate,
    LearningReference,
    LearningService,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    PromotionRegistry,
    SQLiteLearningRepository,
)
from ai_multi_agent_platform.security import RiskClassification
from ai_multi_agent_platform.verification import VerificationOutcome

_TARGET_CASES = (
    (LearningTargetType.AGENT, "agent"),
    (LearningTargetType.SKILL, "skill"),
    (LearningTargetType.MODEL_ROUTING_PROFILE, "model_routing_profile"),
)


@pytest.mark.parametrize(("target_type", "id_prefix"), _TARGET_CASES)
def test_evaluation_gate_binds_exact_target_identity(
    target_type: LearningTargetType,
    id_prefix: str,
) -> None:
    target = LearningTarget(target_type, new_id(id_prefix), 3)
    candidate = _candidate(target)

    matching = _EvaluationStub()
    matching.add("eval-match", target=target)
    LearningQualityGate(evaluation=cast(Any, matching)).enforce(
        _with_evaluation(candidate, "eval-match")
    )

    wrong_resource = _EvaluationStub()
    wrong_resource.add(
        "eval-wrong-resource",
        target=LearningTarget(target_type, new_id(id_prefix), target.revision),
    )
    with pytest.raises(ContractError) as denied_resource:
        LearningQualityGate(evaluation=cast(Any, wrong_resource)).enforce(
            _with_evaluation(candidate, "eval-wrong-resource")
        )
    assert denied_resource.value.code is ErrorCode.CONTRACT_VIOLATION

    wrong_revision = _EvaluationStub()
    wrong_revision.add(
        "eval-wrong-revision",
        target=LearningTarget(target_type, target.resource_id, target.revision + 1),
    )
    with pytest.raises(ContractError) as denied_revision:
        LearningQualityGate(evaluation=cast(Any, wrong_revision)).enforce(
            _with_evaluation(candidate, "eval-wrong-revision")
        )
    assert denied_revision.value.code is ErrorCode.CONTRACT_VIOLATION


@pytest.mark.parametrize(("target_type", "id_prefix"), _TARGET_CASES)
def test_verification_gate_binds_exact_candidate_artifact(
    target_type: LearningTargetType,
    id_prefix: str,
) -> None:
    project_id = new_id("project")
    target = LearningTarget(target_type, new_id(id_prefix), 2)
    artifact = LearningReference(
        kind="artifact",
        resource_id=new_id("artifact"),
        revision="7",
        digest="sha256:matching-candidate-artifact",
    )
    candidate = _candidate(
        target,
        project_id=project_id,
        proposed_artifact_ref=artifact,
        require_evaluation=False,
        require_verification=True,
    )

    matching = _VerificationStub()
    matching.add(
        "verification-match",
        project_id=project_id,
        subject=_subject_for(artifact),
    )
    LearningQualityGate(verification=cast(Any, matching)).enforce(
        _with_verification(candidate, "verification-match")
    )

    unrelated = _VerificationStub()
    unrelated.add(
        "verification-unrelated",
        project_id=project_id,
        subject=SimpleNamespace(
            subject_type="artifact",
            subject_id=new_id("artifact"),
            revision=artifact.revision,
            digest=artifact.digest,
        ),
    )
    with pytest.raises(ContractError) as denied:
        LearningQualityGate(verification=cast(Any, unrelated)).enforce(
            _with_verification(candidate, "verification-unrelated")
        )
    assert denied.value.code is ErrorCode.CONTRACT_VIOLATION


def test_record_gate_evidence_rejects_wrong_target_before_persisting(tmp_path) -> None:
    target = LearningTarget(LearningTargetType.AGENT, new_id("agent"), 4)
    evaluation = _EvaluationStub()
    evaluation.add(
        "eval-other-agent",
        target=LearningTarget(LearningTargetType.AGENT, new_id("agent"), target.revision),
    )
    service = _learning_service(tmp_path, evaluation=evaluation)
    candidate, _ = service.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="exact evaluation binding",
        target=target,
        improvement_type="agent_profile",
        expected_benefit="reject unrelated evidence",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref="user:test",
        source_refs=(LearningReference(kind="operator", resource_id="source"),),
    )

    with pytest.raises(ContractError) as denied:
        service.record_gate_evidence(
            candidate.learning_candidate_id,
            evaluation_run_ids=("eval-other-agent",),
        )
    assert denied.value.code is ErrorCode.CONTRACT_VIOLATION
    stored = service.get_candidate(candidate.learning_candidate_id)
    assert stored.evaluation_run_ids == ()


def test_accept_rechecks_exact_evaluation_target_binding(tmp_path) -> None:
    target = LearningTarget(LearningTargetType.SKILL, new_id("skill"), 2)
    evaluation = _EvaluationStub()
    evaluation.add("eval-skill", target=target)
    service = _learning_service(tmp_path, evaluation=evaluation)
    candidate, _ = service.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="recheck evaluation identity",
        target=target,
        improvement_type="skill",
        expected_benefit="fail closed on changed evidence",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref="user:test",
        source_refs=(LearningReference(kind="operator", resource_id="source"),),
    )
    evaluating = service.record_gate_evidence(
        candidate.learning_candidate_id,
        evaluation_run_ids=("eval-skill",),
    )
    evaluation.add(
        "eval-skill",
        target=LearningTarget(target.resource_type, target.resource_id, target.revision + 1),
    )

    with pytest.raises(ContractError) as denied:
        service.accept(candidate.learning_candidate_id, expected_revision=evaluating.revision)
    assert denied.value.code is ErrorCode.CONTRACT_VIOLATION


def test_regression_free_gate_requires_complete_comparison() -> None:
    target = LearningTarget(LearningTargetType.AGENT, new_id("agent"), 1)
    candidate = _with_evaluation(_candidate(target), "eval-current")

    missing = _EvaluationStub()
    missing.add("eval-current", target=target, comparison=None)
    with pytest.raises(ContractError) as denied_missing:
        LearningQualityGate(evaluation=cast(Any, missing)).enforce(candidate)
    assert denied_missing.value.code is ErrorCode.CONFLICT

    clean = _EvaluationStub()
    clean.add("eval-current", target=target, regressions=())
    LearningQualityGate(evaluation=cast(Any, clean)).enforce(candidate)

    regressed = _EvaluationStub()
    regressed.add("eval-current", target=target, regressions=("regression",))
    with pytest.raises(ContractError) as denied_regression:
        LearningQualityGate(evaluation=cast(Any, regressed)).enforce(candidate)
    assert denied_regression.value.code is ErrorCode.CONFLICT


def test_regression_comparison_refs_must_match_current_and_baseline() -> None:
    target = LearningTarget(LearningTargetType.AGENT, new_id("agent"), 1)
    candidate = _with_evaluation(_candidate(target), "eval-current")
    evaluation = _EvaluationStub()
    evaluation.add(
        "eval-current",
        target=target,
        comparison_current_run_id="different-current",
        comparison_baseline_run_id="baseline-run",
    )

    with pytest.raises(ContractError) as denied:
        LearningQualityGate(evaluation=cast(Any, evaluation)).enforce(candidate)
    assert denied.value.code is ErrorCode.CONTRACT_VIOLATION


def _candidate(
    target: LearningTarget,
    *,
    project_id: str | None = None,
    proposed_artifact_ref: LearningReference | None = None,
    require_evaluation: bool = True,
    require_verification: bool = False,
):
    from ai_multi_agent_platform.learning import LearningCandidate

    return LearningCandidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="identity-bound evidence",
        target=target,
        improvement_type="owner_revision",
        expected_benefit="bind evidence to exact target",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(
            require_evaluation=require_evaluation,
            require_verification=require_verification,
        ),
        creator_ref="user:test",
        source_refs=(LearningReference(kind="operator", resource_id="source"),),
        proposed_artifact_ref=proposed_artifact_ref,
        project_id=project_id,
    )


def _gate_plan(
    *,
    require_evaluation: bool = True,
    require_verification: bool = False,
) -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="identity-bound-gate",
        policy_version=1,
        require_evaluation=require_evaluation,
        require_verification=require_verification,
        require_regression_free=require_evaluation,
        evaluation_suite_refs=("learning-suite@1",) if require_evaluation else (),
        verification_policy_refs=("learning-verification@1",) if require_verification else (),
    )


def _with_evaluation(candidate, run_id: str):
    from dataclasses import replace

    return replace(candidate, evaluation_run_ids=(run_id,))


def _with_verification(candidate, verification_id: str):
    from dataclasses import replace

    return replace(candidate, verification_ids=(verification_id,))


def _subject_for(reference: LearningReference) -> SimpleNamespace:
    return SimpleNamespace(
        subject_type=reference.kind,
        subject_id=reference.resource_id,
        revision=reference.revision,
        digest=reference.digest,
    )


def _learning_service(tmp_path, *, evaluation: _EvaluationStub) -> LearningService:
    return LearningService(
        SQLiteLearningRepository(tmp_path / "learning.sqlite3"),
        quality_gate=LearningQualityGate(evaluation=cast(Any, evaluation)),
        promotion_registry=PromotionRegistry(()),
        authorization_gate=cast(Any, object()),
    )


class _EvaluationStub:
    def __init__(self) -> None:
        self._details: dict[str, SimpleNamespace] = {}

    def add(
        self,
        run_id: str,
        *,
        target: LearningTarget,
        comparison: object = ...,
        regressions: tuple[object, ...] = (),
        comparison_current_run_id: str | None = None,
        comparison_baseline_run_id: str | None = None,
    ) -> None:
        reference = SimpleNamespace(
            kind=target.resource_type.value,
            ref_id=target.resource_id,
            version=str(target.revision),
            revision=None,
        )
        baseline_run_id = "baseline-run"
        if comparison is ...:
            comparison = SimpleNamespace(
                current_run_id=comparison_current_run_id or run_id,
                baseline_run_id=comparison_baseline_run_id or baseline_run_id,
                regressions=regressions,
            )
        self._details[run_id] = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                status=EvaluationRunStatus.COMPLETED,
                baseline_run_id=baseline_run_id,
                snapshot=SimpleNamespace(references=(reference,)),
            ),
            results=(
                SimpleNamespace(
                    result_id=f"{run_id}-result",
                    outcome=EvaluationOutcome.PASSED,
                ),
            ),
            comparison=comparison,
            aggregates=(),
            manifest=SimpleNamespace(
                evaluation_run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                configuration_references=(reference,),
                skill_bundles=(),
                context_bundles=(),
                research_evidence=(),
                fixture_sources=(),
                verification_policies=(),
                dependencies=(),
                contract_versions=(),
                workspace=None,
            ),
        )

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        return self._details[run_id]

    def get_suite(self, suite_ref: str) -> SimpleNamespace:
        assert suite_ref == "learning-suite@1"
        return SimpleNamespace(cases=())


class _VerificationStub:
    def __init__(self) -> None:
        self._requests: dict[str, SimpleNamespace] = {}
        self._results: dict[str, SimpleNamespace] = {}

    def add(self, verification_id: str, *, project_id: str, subject: SimpleNamespace) -> None:
        self._requests[verification_id] = SimpleNamespace(
            verification_id=verification_id,
            policy_id="learning-verification",
            policy_version=1,
            project_id=project_id,
            subject=subject,
            producer=None,
        )
        self._results[verification_id] = SimpleNamespace(
            verification_result_id=f"{verification_id}-result",
            outcome=VerificationOutcome.PASS,
            subject=subject,
        )

    def get_request(self, verification_id: str) -> SimpleNamespace:
        return self._requests[verification_id]

    def result_for(self, verification_id: str) -> SimpleNamespace | None:
        return self._results.get(verification_id)
