"""Deterministic rubric scoring over explicit observation evidence."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    AssertionResult,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
)

RubricScores = Mapping[str, tuple[float, str]]


def build_rubric_result(
    *,
    descriptor: EvaluatorDescriptor,
    evaluation_run_id: str,
    case: EvaluationCase,
    observation: EvaluationObservation,
    scores: RubricScores,
) -> EvaluationResult:
    """Build one weighted canonical result from exact per-criterion scores."""

    if not case.rubric:
        return EvaluationResult(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            case_version=case.version,
            evaluator=descriptor,
            outcome=EvaluationOutcome.PASSED,
            deterministic_pass=True if descriptor.deterministic else None,
            score=1.0,
            case_tags=case.tags,
            task_id=observation.task_id,
            run_id=observation.run_id,
            artifact_refs=observation.artifact_refs,
            telemetry_refs=observation.telemetry_refs,
        )

    expected_ids = {criterion.criterion_id for criterion in case.rubric}
    supplied_ids = set(scores)
    missing = sorted(expected_ids - supplied_ids)
    extra = sorted(supplied_ids - expected_ids)
    if missing:
        raise ValueError(f"rubric scores are missing criteria: {', '.join(missing)}")
    if extra:
        raise ValueError(f"rubric scores contain unknown criteria: {', '.join(extra)}")

    assertions: list[AssertionResult] = []
    weighted_score = 0.0
    total_weight = 0.0
    passed = True
    for criterion in case.rubric:
        score, explanation = scores[criterion.criterion_id]
        if not 0.0 <= score <= 1.0:
            raise ValueError(
                f"rubric score for {criterion.criterion_id} must be between 0.0 and 1.0"
            )
        criterion_passed = score >= criterion.minimum_score
        passed = passed and criterion_passed
        weighted_score += score * criterion.weight
        total_weight += criterion.weight
        assertions.append(
            AssertionResult(
                assertion_id=f"rubric:{criterion.criterion_id}",
                passed=criterion_passed,
                message=explanation or criterion.description,
                expected=criterion.minimum_score,
                actual=score,
            )
        )

    overall_score = weighted_score / total_weight
    return EvaluationResult(
        evaluation_run_id=evaluation_run_id,
        case_id=case.case_id,
        case_version=case.version,
        evaluator=descriptor,
        outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
        deterministic_pass=passed if descriptor.deterministic else None,
        score=overall_score,
        assertions=tuple(assertions),
        case_tags=case.tags,
        task_id=observation.task_id,
        run_id=observation.run_id,
        artifact_refs=observation.artifact_refs,
        telemetry_refs=observation.telemetry_refs,
    )


class ObservationRubricEvaluator:
    """Score a rubric from explicit criterion scores projected into observation data.

    This evaluator deliberately does not infer semantic quality. Executors, test
    harnesses or human-review adapters can project criterion scores under
    ``rubric_scores`` and this evaluator performs deterministic validation,
    thresholding and weighted aggregation.
    """

    descriptor = EvaluatorDescriptor(
        evaluator_id="reference.rubric-observation",
        kind=EvaluatorKind.RUBRIC,
        version="1.0",
        deterministic=True,
        configuration_ref="observation.rubric_scores@1",
    )

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        if not case.rubric:
            return build_rubric_result(
                descriptor=self.descriptor,
                evaluation_run_id=evaluation_run_id,
                case=case,
                observation=observation,
                scores={},
            )

        raw_scores = observation.data.get("rubric_scores")
        if not isinstance(raw_scores, Mapping):
            raise ValueError("observation.data.rubric_scores must be an object")

        scores: dict[str, tuple[float, str]] = {}
        for criterion_id, raw_value in raw_scores.items():
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                raise ValueError("rubric score criterion IDs must be non-blank strings")
            score: float
            explanation = ""
            if isinstance(raw_value, bool):
                raise ValueError(f"rubric score for {criterion_id} must be numeric or an object")
            if isinstance(raw_value, int | float):
                score = float(raw_value)
            elif isinstance(raw_value, Mapping):
                unknown = set(raw_value) - {"score", "explanation"}
                if unknown:
                    raise ValueError(
                        f"rubric score for {criterion_id} contains unknown fields: "
                        f"{', '.join(sorted(str(item) for item in unknown))}"
                    )
                raw_score = raw_value.get("score")
                if isinstance(raw_score, bool) or not isinstance(raw_score, int | float):
                    raise ValueError(f"rubric score for {criterion_id} must be numeric")
                score = float(raw_score)
                raw_explanation = raw_value.get("explanation", "")
                if not isinstance(raw_explanation, str):
                    raise ValueError(f"rubric explanation for {criterion_id} must be a string")
                explanation = raw_explanation
            else:
                raise ValueError(f"rubric score for {criterion_id} must be numeric or an object")
            scores[criterion_id] = (score, explanation)

        return build_rubric_result(
            descriptor=self.descriptor,
            evaluation_run_id=evaluation_run_id,
            case=case,
            observation=observation,
            scores=scores,
        )


__all__ = ["ObservationRubricEvaluator", "RubricScores", "build_rubric_result"]
