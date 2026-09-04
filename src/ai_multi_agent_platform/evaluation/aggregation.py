"""Versioned stochastic result aggregation for repeated evaluation runs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from statistics import mean, median

from ai_multi_agent_platform.domain import new_id

from .models import (
    ComparisonOperator,
    EvaluationOutcome,
    EvaluationResult,
    EvaluatorDescriptor,
    MetricResult,
    utc_now,
)


class AggregationMethod(StrEnum):
    """Supported deterministic reductions for numeric sample values."""

    MEAN = "mean"
    MEDIAN = "median"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class AggregationPolicy:
    """Explicit versioned rules for reducing repeated evaluation samples."""

    policy_id: str
    version: str
    score_method: AggregationMethod = AggregationMethod.MEAN
    metric_method: AggregationMethod = AggregationMethod.MEAN
    minimum_pass_rate: float = 1.0
    fail_on_error: bool = True
    require_equal_sample_count: bool = True

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("aggregation policy_id must not be blank")
        if not self.version.strip():
            raise ValueError("aggregation policy version must not be blank")
        if not 0.0 <= self.minimum_pass_rate <= 1.0:
            raise ValueError("aggregation minimum_pass_rate must be between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class AggregationSampleRef:
    """Provenance for one raw evaluator result contributing to an aggregate."""

    result_id: str
    repetition_index: int
    seed: int | None
    outcome: EvaluationOutcome

    def __post_init__(self) -> None:
        if not self.result_id.strip():
            raise ValueError("aggregation sample result_id must not be blank")
        if self.repetition_index < 0:
            raise ValueError("aggregation sample repetition_index must be >= 0")


@dataclass(frozen=True, slots=True)
class AggregatedEvaluationResult:
    """Comparable result derived from an explicit set of raw repetition results."""

    evaluation_run_id: str
    case_id: str
    case_version: str
    evaluator: EvaluatorDescriptor
    outcome: EvaluationOutcome
    aggregation_policy_id: str
    aggregation_policy_version: str
    samples: tuple[AggregationSampleRef, ...]
    deterministic_pass: bool | None = None
    score: float | None = None
    metrics: tuple[MetricResult, ...] = ()
    case_tags: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    telemetry_refs: tuple[str, ...] = ()
    result_id: str = field(default_factory=lambda: new_id("evaluation_aggregate"))
    created_at: object = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.evaluation_run_id.strip():
            raise ValueError("aggregate evaluation_run_id must not be blank")
        if not self.case_id.strip():
            raise ValueError("aggregate case_id must not be blank")
        if not self.case_version.strip():
            raise ValueError("aggregate case_version must not be blank")
        if not self.aggregation_policy_id.strip():
            raise ValueError("aggregate aggregation_policy_id must not be blank")
        if not self.aggregation_policy_version.strip():
            raise ValueError("aggregate aggregation_policy_version must not be blank")
        if not self.samples:
            raise ValueError("aggregate must contain at least one sample")
        if self.score is not None and not 0.0 <= self.score <= 1.0:
            raise ValueError("aggregate score must be between 0.0 and 1.0")
        result_ids = [sample.result_id for sample in self.samples]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("aggregate sample result IDs must be unique")
        repetition_indices = [sample.repetition_index for sample in self.samples]
        if len(repetition_indices) != len(set(repetition_indices)):
            raise ValueError("aggregate repetition indices must be unique")

    @property
    def sample_count(self) -> int:
        return len(self.samples)

    @property
    def passed_count(self) -> int:
        return sum(sample.outcome is EvaluationOutcome.PASSED for sample in self.samples)

    @property
    def failed_count(self) -> int:
        return sum(sample.outcome is EvaluationOutcome.FAILED for sample in self.samples)

    @property
    def error_count(self) -> int:
        return sum(sample.outcome is EvaluationOutcome.ERROR for sample in self.samples)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.sample_count


ComparableEvaluationResult = EvaluationResult | AggregatedEvaluationResult


def _aggregate_id(
    *,
    evaluation_run_id: str,
    case_id: str,
    evaluator_id: str,
    policy: AggregationPolicy,
) -> str:
    payload = "\x1f".join(
        (evaluation_run_id, case_id, evaluator_id, policy.policy_id, policy.version)
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"evaluation_aggregate_{digest}"


def _reduce(values: tuple[float, ...], method: AggregationMethod) -> float:
    if not values:
        raise ValueError("cannot aggregate an empty numeric sample")
    if method is AggregationMethod.MEAN:
        return float(mean(values))
    if method is AggregationMethod.MEDIAN:
        return float(median(values))
    if method is AggregationMethod.MINIMUM:
        return min(values)
    if method is AggregationMethod.MAXIMUM:
        return max(values)
    raise ValueError(f"unsupported aggregation method: {method}")


def _metric_passes(value: float, operator: ComparisonOperator, threshold: float) -> bool:
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


def _unique(values: tuple[str | None, ...]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value is not None}))


def _aggregate_metrics(
    results: tuple[EvaluationResult, ...],
    *,
    policy: AggregationPolicy,
) -> tuple[MetricResult, ...]:
    names = sorted({metric.metric_name for result in results for metric in result.metrics})
    aggregated: list[MetricResult] = []
    for name in names:
        per_result: list[MetricResult] = []
        complete = True
        for result in results:
            matches = [metric for metric in result.metrics if metric.metric_name == name]
            if len(matches) != 1:
                complete = False
                break
            per_result.append(matches[0])
        if not complete:
            # Absence remains absence; regression policy can classify a required missing metric.
            continue

        units = {metric.unit for metric in per_result}
        thresholds = {metric.threshold for metric in per_result}
        operators = {metric.operator for metric in per_result}
        if len(units) != 1 or len(thresholds) != 1 or len(operators) != 1:
            raise ValueError(
                f"metric {name!r} metadata changed across repetitions and cannot be aggregated"
            )
        value = _reduce(tuple(metric.value for metric in per_result), policy.metric_method)
        threshold = per_result[0].threshold
        operator = per_result[0].operator
        passed = (
            None
            if threshold is None or operator is None
            else _metric_passes(value, operator, threshold)
        )
        aggregated.append(
            MetricResult(
                metric_name=name,
                value=value,
                passed=passed,
                threshold=threshold,
                operator=operator,
                unit=per_result[0].unit,
            )
        )
    return tuple(aggregated)


class ResultAggregator:
    """Reduce raw repetition results without hiding the samples that produced them."""

    def aggregate(
        self,
        *,
        results: tuple[EvaluationResult, ...],
        policy: AggregationPolicy,
        expected_repetitions: int,
    ) -> tuple[AggregatedEvaluationResult, ...]:
        if expected_repetitions <= 0:
            raise ValueError("expected_repetitions must be greater than zero")
        grouped: dict[tuple[str, str, str], list[EvaluationResult]] = {}
        for result in results:
            key = (result.case_id, result.case_version, result.evaluator.evaluator_id)
            grouped.setdefault(key, []).append(result)

        aggregates: list[AggregatedEvaluationResult] = []
        for key in sorted(grouped):
            group = tuple(sorted(grouped[key], key=lambda item: item.repetition_index))
            case_id, case_version, evaluator_id = key
            if len(group) != expected_repetitions:
                raise ValueError(
                    f"case {case_id!r} evaluator {evaluator_id!r} has {len(group)} samples; "
                    f"expected {expected_repetitions}"
                )
            expected_indices = tuple(range(expected_repetitions))
            actual_indices = tuple(item.repetition_index for item in group)
            if actual_indices != expected_indices:
                raise ValueError(
                    f"case {case_id!r} evaluator {evaluator_id!r} repetition indices "
                    f"must be {expected_indices}, got {actual_indices}"
                )
            run_ids = {item.evaluation_run_id for item in group}
            if len(run_ids) != 1:
                raise ValueError("aggregation group cannot span evaluation runs")
            evaluators = {item.evaluator for item in group}
            if len(evaluators) != 1:
                raise ValueError("evaluator descriptor changed across repetitions")
            tags = {item.case_tags for item in group}
            if len(tags) != 1:
                raise ValueError("case tags changed across repetitions")

            passed_count = sum(item.outcome is EvaluationOutcome.PASSED for item in group)
            error_count = sum(item.outcome is EvaluationOutcome.ERROR for item in group)
            pass_rate = passed_count / len(group)
            if policy.fail_on_error and error_count:
                outcome = EvaluationOutcome.ERROR
            elif pass_rate >= policy.minimum_pass_rate:
                outcome = EvaluationOutcome.PASSED
            else:
                outcome = EvaluationOutcome.FAILED

            raw_scores = tuple(item.score for item in group)
            score = (
                None
                if any(value is None for value in raw_scores)
                else _reduce(tuple(float(value) for value in raw_scores if value is not None), policy.score_method)
            )
            evaluator = group[0].evaluator
            deterministic_pass = (
                outcome is EvaluationOutcome.PASSED if evaluator.deterministic else None
            )
            samples = tuple(
                AggregationSampleRef(
                    result_id=item.result_id,
                    repetition_index=item.repetition_index,
                    seed=item.seed,
                    outcome=item.outcome,
                )
                for item in group
            )
            aggregates.append(
                AggregatedEvaluationResult(
                    evaluation_run_id=group[0].evaluation_run_id,
                    case_id=case_id,
                    case_version=case_version,
                    evaluator=evaluator,
                    outcome=outcome,
                    deterministic_pass=deterministic_pass,
                    score=score,
                    metrics=_aggregate_metrics(group, policy=policy),
                    case_tags=group[0].case_tags,
                    task_ids=_unique(tuple(item.task_id for item in group)),
                    run_ids=_unique(tuple(item.run_id for item in group)),
                    artifact_refs=_unique(
                        tuple(ref for item in group for ref in item.artifact_refs)
                    ),
                    telemetry_refs=_unique(
                        tuple(ref for item in group for ref in item.telemetry_refs)
                    ),
                    aggregation_policy_id=policy.policy_id,
                    aggregation_policy_version=policy.version,
                    samples=samples,
                    result_id=_aggregate_id(
                        evaluation_run_id=group[0].evaluation_run_id,
                        case_id=case_id,
                        evaluator_id=evaluator_id,
                        policy=policy,
                    ),
                )
            )
        return tuple(aggregates)


__all__ = [
    "AggregatedEvaluationResult",
    "AggregationMethod",
    "AggregationPolicy",
    "AggregationSampleRef",
    "ComparableEvaluationResult",
    "ResultAggregator",
]
