from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.evaluation import (
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluatorKind,
    ObservationRubricEvaluator,
    RubricCriterion,
    evaluate_safely,
    parse_evaluation_suite,
)


def _rubric_case() -> EvaluationCase:
    return EvaluationCase(
        case_id="case.rubric",
        name="rubric evaluation",
        version="3",
        rubric=(
            RubricCriterion(
                criterion_id="correctness",
                description="The answer is factually correct.",
                weight=2.0,
                minimum_score=0.8,
            ),
            RubricCriterion(
                criterion_id="clarity",
                description="The answer is clear and understandable.",
                weight=1.0,
                minimum_score=0.7,
            ),
        ),
        tags=("qualitative",),
    )


def test_observation_rubric_evaluator_applies_weighted_versioned_thresholds() -> None:
    evaluator = ObservationRubricEvaluator()
    result = evaluator.evaluate(
        evaluation_run_id="evaluation_run_rubric",
        case=_rubric_case(),
        observation=EvaluationObservation(
            data={
                "rubric_scores": {
                    "correctness": {"score": 0.9, "explanation": "Correct."},
                    "clarity": {"score": 0.75, "explanation": "Clear."},
                }
            },
            task_id="task_rubric",
            run_id="run_rubric",
            artifact_refs=("artifact_rubric",),
            telemetry_refs=("telemetry_rubric",),
        ),
    )

    assert result.outcome is EvaluationOutcome.PASSED
    assert result.deterministic_pass is True
    assert result.score == pytest.approx(0.85)
    assert result.task_id == "task_rubric"
    assert result.run_id == "run_rubric"
    assert result.artifact_refs == ("artifact_rubric",)
    assert result.telemetry_refs == ("telemetry_rubric",)
    by_id = {item.assertion_id: item for item in result.assertions}
    assert by_id["rubric:correctness"].passed is True
    assert by_id["rubric:correctness"].expected == 0.8
    assert by_id["rubric:correctness"].actual == 0.9
    assert by_id["rubric:clarity"].passed is True


def test_observation_rubric_evaluator_fails_below_case_owned_minimum_score() -> None:
    result = ObservationRubricEvaluator().evaluate(
        evaluation_run_id="evaluation_run_rubric_fail",
        case=_rubric_case(),
        observation=EvaluationObservation(
            data={"rubric_scores": {"correctness": 0.79, "clarity": 1.0}}
        ),
    )

    assert result.outcome is EvaluationOutcome.FAILED
    assert result.deterministic_pass is False
    assert result.score == pytest.approx((0.79 * 2.0 + 1.0) / 3.0)
    failed = [item for item in result.assertions if not item.passed]
    assert [item.assertion_id for item in failed] == ["rubric:correctness"]


def test_rubric_evaluator_failure_is_contained_as_canonical_error_result() -> None:
    result = asyncio.run(
        evaluate_safely(
            ObservationRubricEvaluator(),
            evaluation_run_id="evaluation_run_bad_rubric",
            case=_rubric_case(),
            observation=EvaluationObservation(data={}),
        )
    )

    assert result.outcome is EvaluationOutcome.ERROR
    assert result.error_category == "evaluator_failure"
    assert "rubric_scores" in (result.error_message or "")
    assert result.evaluator.kind is EvaluatorKind.RUBRIC


def test_strict_suite_parser_accepts_and_validates_rubric_minimum_score() -> None:
    suite = parse_evaluation_suite(
        {
            "suite_id": "suite.rubric",
            "name": "rubric suite",
            "version": "1",
            "cases": [
                {
                    "case_id": "case.rubric",
                    "name": "rubric case",
                    "version": "1",
                    "rubric": [
                        {
                            "criterion_id": "quality",
                            "description": "Quality criterion",
                            "weight": 2.0,
                            "minimum_score": 0.75,
                        }
                    ],
                }
            ],
        }
    )

    criterion = suite.cases[0].rubric[0]
    assert criterion.weight == 2.0
    assert criterion.minimum_score == 0.75

    with pytest.raises(ValueError, match="minimum_score"):
        parse_evaluation_suite(
            {
                "suite_id": "suite.invalid-rubric",
                "name": "invalid rubric suite",
                "version": "1",
                "cases": [
                    {
                        "case_id": "case.invalid",
                        "name": "invalid",
                        "version": "1",
                        "rubric": [
                            {
                                "criterion_id": "quality",
                                "description": "Quality criterion",
                                "minimum_score": 1.1,
                            }
                        ],
                    }
                ],
            }
        )
