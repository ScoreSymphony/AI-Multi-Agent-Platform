from __future__ import annotations

import pytest

from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
    RegressionEngine,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
)


def _result(
    *,
    run_id: str,
    evaluator_id: str,
    passed: bool,
    case_id: str = "case.shared",
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_run_id=run_id,
        case_id=case_id,
        case_version="1",
        evaluator=EvaluatorDescriptor(
            evaluator_id=evaluator_id,
            kind=EvaluatorKind.DETERMINISTIC,
            version="1",
            deterministic=True,
        ),
        outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
        deterministic_pass=passed,
        score=1.0 if passed else 0.0,
    )


def _policy() -> RegressionPolicy:
    return RegressionPolicy(
        policy_id="policy.identity",
        version="1",
        rules=(
            RegressionRule(
                rule_id="pass-to-fail",
                kind=RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL,
            ),
        ),
    )


def test_regression_engine_matches_baseline_by_case_and_evaluator() -> None:
    report = RegressionEngine().compare(
        baseline_run_id="evaluation_run_baseline",
        current_run_id="evaluation_run_current",
        baseline_results=(
            _result(run_id="evaluation_run_baseline", evaluator_id="eval.a", passed=True),
            _result(run_id="evaluation_run_baseline", evaluator_id="eval.b", passed=False),
        ),
        current_results=(
            _result(run_id="evaluation_run_current", evaluator_id="eval.a", passed=False),
            _result(run_id="evaluation_run_current", evaluator_id="eval.b", passed=True),
        ),
        policy=_policy(),
    )

    assert len(report.regressions) == 1
    assert len(report.improvements) == 1
    assert report.regressions[0].baseline_result_id is not None
    assert report.improvements[0].baseline_result_id is not None


def test_regression_engine_rejects_unaggregated_duplicate_case_evaluator_results() -> None:
    duplicate = _result(
        run_id="evaluation_run_baseline",
        evaluator_id="eval.a",
        passed=True,
    )

    with pytest.raises(ValueError, match="multiple unaggregated results"):
        RegressionEngine().compare(
            baseline_run_id="evaluation_run_baseline",
            current_run_id="evaluation_run_current",
            baseline_results=(duplicate, duplicate),
            current_results=(
                _result(run_id="evaluation_run_current", evaluator_id="eval.a", passed=True),
            ),
            policy=_policy(),
        )
