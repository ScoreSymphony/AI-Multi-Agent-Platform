"""Reference evaluators and safe evaluator execution helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping, Sequence
from inspect import isawaitable
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .contracts import Evaluator, EvaluatorLike
from .hardening import observation_assertion_payload
from .models import (
    AssertionResult,
    ComparisonOperator,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
)

_MISSING = object()


def _resolve_path(data: JsonValue, path: str) -> JsonValue | object:
    current: JsonValue | object = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            if part not in current:
                return _MISSING
            current = current[part]
            continue
        if isinstance(current, Sequence) and not isinstance(current, str | bytes):
            try:
                index = int(part)
            except ValueError:
                return _MISSING
            if index < 0 or index >= len(current):
                return _MISSING
            current = current[index]
            continue
        return _MISSING
    return current


def _numeric(value: JsonValue | object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _compare(actual: JsonValue | object, operator: ComparisonOperator, expected: JsonValue) -> bool:
    if operator is ComparisonOperator.EXISTS:
        return actual is not _MISSING
    if operator is ComparisonOperator.NOT_EXISTS:
        return actual is _MISSING
    if actual is _MISSING:
        return False
    if operator is ComparisonOperator.EQ:
        return actual == expected
    if operator is ComparisonOperator.NE:
        return actual != expected
    if operator is ComparisonOperator.CONTAINS:
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        if isinstance(actual, Sequence) and not isinstance(actual, str | bytes):
            return expected in actual
        if isinstance(actual, Mapping) and isinstance(expected, str):
            return expected in actual
        return False

    actual_number = _numeric(actual)
    expected_number = _numeric(expected)
    if actual_number is None or expected_number is None:
        return False
    if operator is ComparisonOperator.GT:
        return actual_number > expected_number
    if operator is ComparisonOperator.GTE:
        return actual_number >= expected_number
    if operator is ComparisonOperator.LT:
        return actual_number < expected_number
    if operator is ComparisonOperator.LTE:
        return actual_number <= expected_number
    raise ValueError(f"unsupported comparison operator: {operator}")


def _metric_compare(value: float, operator: ComparisonOperator, threshold: float) -> bool:
    if operator is ComparisonOperator.GT:
        return value > threshold
    if operator is ComparisonOperator.GTE:
        return value >= threshold
    if operator is ComparisonOperator.LT:
        return value < threshold
    if operator is ComparisonOperator.LTE:
        return value <= threshold
    if operator is ComparisonOperator.EQ:
        return value == threshold
    if operator is ComparisonOperator.NE:
        return value != threshold
    raise ValueError(f"unsupported metric comparison operator: {operator}")


def _error_result(
    *,
    descriptor: EvaluatorDescriptor,
    evaluation_run_id: str,
    case: EvaluationCase,
    observation: EvaluationObservation,
    error: Exception,
) -> EvaluationResult:
    return EvaluationResult(
        evaluation_run_id=evaluation_run_id,
        case_id=case.case_id,
        case_version=case.version,
        evaluator=descriptor,
        outcome=EvaluationOutcome.ERROR,
        case_tags=case.tags,
        task_id=observation.task_id,
        run_id=observation.run_id,
        artifact_refs=observation.artifact_refs,
        telemetry_refs=observation.telemetry_refs,
        error_category="evaluator_failure",
        error_message=str(error),
    )


class DeterministicAssertionEvaluator:
    """Evaluate explicit structured assertions without any LLM dependency."""

    descriptor = EvaluatorDescriptor(
        evaluator_id="reference.deterministic",
        kind=EvaluatorKind.DETERMINISTIC,
        version="1.0",
        deterministic=True,
    )

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        results: list[AssertionResult] = []
        assertion_payload = observation_assertion_payload(observation)
        for assertion in case.assertions:
            actual = _resolve_path(assertion_payload, assertion.path)
            passed = _compare(actual, assertion.operator, assertion.expected)
            results.append(
                AssertionResult(
                    assertion_id=assertion.assertion_id,
                    passed=passed,
                    message=assertion.message
                    or f"{assertion.path} {assertion.operator.value} assertion",
                    expected=assertion.expected,
                    actual=None if actual is _MISSING else cast(JsonValue, actual),
                )
            )
        passed = all(item.passed for item in results)
        return EvaluationResult(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            case_version=case.version,
            evaluator=self.descriptor,
            outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
            deterministic_pass=passed,
            score=1.0 if passed else 0.0,
            assertions=tuple(results),
            case_tags=case.tags,
            task_id=observation.task_id,
            run_id=observation.run_id,
            artifact_refs=observation.artifact_refs,
            telemetry_refs=observation.telemetry_refs,
        )


class MetricThresholdEvaluator:
    """Evaluate versioned metric thresholds declared by the evaluation case."""

    descriptor = EvaluatorDescriptor(
        evaluator_id="reference.metric-threshold",
        kind=EvaluatorKind.METRIC,
        version="1.0",
        deterministic=True,
    )

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        results: list[MetricResult] = []
        passed = True
        for rule in case.metric_rules:
            value = observation.metrics.get(rule.metric_name)
            if value is None:
                passed = False
                continue
            rule_passed = _metric_compare(value, rule.operator, rule.threshold)
            passed = passed and rule_passed
            results.append(
                MetricResult(
                    metric_name=rule.metric_name,
                    value=value,
                    passed=rule_passed,
                    threshold=rule.threshold,
                    operator=rule.operator,
                    unit=rule.unit,
                )
            )
        return EvaluationResult(
            evaluation_run_id=evaluation_run_id,
            case_id=case.case_id,
            case_version=case.version,
            evaluator=self.descriptor,
            outcome=EvaluationOutcome.PASSED if passed else EvaluationOutcome.FAILED,
            deterministic_pass=passed,
            score=1.0 if passed else 0.0,
            metrics=tuple(results),
            case_tags=case.tags,
            task_id=observation.task_id,
            run_id=observation.run_id,
            artifact_refs=observation.artifact_refs,
            telemetry_refs=observation.telemetry_refs,
        )


class SafeEvaluator:
    """Contain failures from synchronous evaluators as explicit canonical results."""

    def __init__(self, evaluator: Evaluator) -> None:
        self._evaluator = evaluator

    @property
    def descriptor(self) -> EvaluatorDescriptor:
        return self._evaluator.descriptor

    def evaluate(
        self,
        *,
        evaluation_run_id: str,
        case: EvaluationCase,
        observation: EvaluationObservation,
    ) -> EvaluationResult:
        try:
            return self._evaluator.evaluate(
                evaluation_run_id=evaluation_run_id,
                case=case,
                observation=observation,
            )
        except Exception as exc:
            return _error_result(
                descriptor=self.descriptor,
                evaluation_run_id=evaluation_run_id,
                case=case,
                observation=observation,
                error=exc,
            )


async def evaluate_safely(
    evaluator: EvaluatorLike,
    *,
    evaluation_run_id: str,
    case: EvaluationCase,
    observation: EvaluationObservation,
) -> EvaluationResult:
    """Run either sync or async evaluators and contain evaluator-local failures."""

    try:
        candidate = evaluator.evaluate(
            evaluation_run_id=evaluation_run_id,
            case=case,
            observation=observation,
        )
        if isawaitable(candidate):
            return await cast(Awaitable[EvaluationResult], candidate)
        return candidate
    except Exception as exc:
        return _error_result(
            descriptor=evaluator.descriptor,
            evaluation_run_id=evaluation_run_id,
            case=case,
            observation=observation,
            error=exc,
        )
