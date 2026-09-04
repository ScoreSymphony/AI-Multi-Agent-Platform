"""Baseline comparison and versioned regression policy engine."""

from __future__ import annotations

from .aggregation import ComparableEvaluationResult
from .models import (
    ComparisonFinding,
    ComparisonKind,
    ComparisonOperator,
    ComparisonReport,
    EvaluationOutcome,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
)


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
    raise ValueError(f"unsupported metric operator: {operator}")


def _metric_value(result: ComparableEvaluationResult, metric_name: str) -> float | None:
    for metric in result.metrics:
        if metric.metric_name == metric_name:
            return metric.value
    return None


def _result_key(result: ComparableEvaluationResult) -> tuple[str, str]:
    """Match baselines by canonical case and evaluator identity."""

    return result.case_id, result.evaluator.evaluator_id


def _index_results(
    results: tuple[ComparableEvaluationResult, ...],
    *,
    label: str,
) -> dict[tuple[str, str], ComparableEvaluationResult]:
    indexed: dict[tuple[str, str], ComparableEvaluationResult] = {}
    for result in results:
        key = _result_key(result)
        if key in indexed:
            case_id, evaluator_id = key
            raise ValueError(
                f"{label} contains multiple unaggregated results for "
                f"case {case_id!r} and evaluator {evaluator_id!r}"
            )
        indexed[key] = result
    return indexed


class RegressionEngine:
    """Compare current results to an accepted baseline under explicit policy."""

    def compare(
        self,
        *,
        baseline_run_id: str,
        current_run_id: str,
        baseline_results: tuple[ComparableEvaluationResult, ...],
        current_results: tuple[ComparableEvaluationResult, ...],
        policy: RegressionPolicy,
    ) -> ComparisonReport:
        baseline = _index_results(baseline_results, label="baseline")
        _index_results(current_results, label="current")
        findings: list[ComparisonFinding] = []

        for current in current_results:
            previous = baseline.get(_result_key(current))
            for rule in policy.rules:
                finding = self._apply_rule(rule, previous, current)
                if finding is not None:
                    findings.append(finding)

        return ComparisonReport(
            baseline_run_id=baseline_run_id,
            current_run_id=current_run_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
            findings=tuple(findings),
        )

    def _apply_rule(
        self,
        rule: RegressionRule,
        baseline: ComparableEvaluationResult | None,
        current: ComparableEvaluationResult,
    ) -> ComparisonFinding | None:
        if rule.kind is RegressionRuleKind.DETERMINISTIC_PASS_TO_FAIL:
            if baseline is None:
                return None
            if baseline.deterministic_pass is True and current.deterministic_pass is False:
                return self._finding(
                    ComparisonKind.REGRESSION,
                    rule,
                    baseline,
                    current,
                    "deterministic result changed from pass to fail",
                )
            if baseline.deterministic_pass is False and current.deterministic_pass is True:
                return self._finding(
                    ComparisonKind.IMPROVEMENT,
                    rule,
                    baseline,
                    current,
                    "deterministic result changed from fail to pass",
                )
            return None

        if rule.kind is RegressionRuleKind.SCORE_DROP:
            if baseline is None or baseline.score is None or current.score is None:
                return None
            threshold = rule.threshold
            assert threshold is not None
            delta = current.score - baseline.score
            if delta < -threshold:
                return self._finding(
                    ComparisonKind.REGRESSION,
                    rule,
                    baseline,
                    current,
                    f"score dropped by {abs(delta):.4f}, threshold {threshold:.4f}",
                )
            if delta > threshold:
                return self._finding(
                    ComparisonKind.IMPROVEMENT,
                    rule,
                    baseline,
                    current,
                    f"score improved by {delta:.4f}, threshold {threshold:.4f}",
                )
            return None

        if rule.kind is RegressionRuleKind.TAGGED_CASE_FAILURE:
            assert rule.tag is not None
            if rule.tag not in current.case_tags:
                return None
            current_failed = current.outcome is not EvaluationOutcome.PASSED
            baseline_passed = baseline is not None and baseline.outcome is EvaluationOutcome.PASSED
            if current_failed and (baseline is None or baseline_passed):
                return self._finding(
                    ComparisonKind.REGRESSION,
                    rule,
                    baseline,
                    current,
                    f"tagged case '{rule.tag}' failed",
                )
            if (
                baseline is not None
                and baseline.outcome is not EvaluationOutcome.PASSED
                and current.outcome is EvaluationOutcome.PASSED
            ):
                return self._finding(
                    ComparisonKind.IMPROVEMENT,
                    rule,
                    baseline,
                    current,
                    f"tagged case '{rule.tag}' recovered",
                )
            return None

        if rule.kind is RegressionRuleKind.METRIC_THRESHOLD:
            assert rule.metric_name is not None
            assert rule.metric_operator is not None
            assert rule.threshold is not None
            current_value = _metric_value(current, rule.metric_name)
            if current_value is None:
                return self._finding(
                    ComparisonKind.REGRESSION,
                    rule,
                    baseline,
                    current,
                    f"required metric '{rule.metric_name}' is missing",
                )
            current_passes = _metric_passes(
                current_value,
                rule.metric_operator,
                rule.threshold,
            )
            baseline_value = None if baseline is None else _metric_value(baseline, rule.metric_name)
            baseline_passes = (
                None
                if baseline_value is None
                else _metric_passes(baseline_value, rule.metric_operator, rule.threshold)
            )
            if not current_passes and (baseline_passes is not False):
                return self._finding(
                    ComparisonKind.REGRESSION,
                    rule,
                    baseline,
                    current,
                    (
                        f"metric '{rule.metric_name}' value {current_value} violates "
                        f"{rule.metric_operator.value} {rule.threshold}"
                    ),
                )
            if current_passes and baseline_passes is False:
                return self._finding(
                    ComparisonKind.IMPROVEMENT,
                    rule,
                    baseline,
                    current,
                    f"metric '{rule.metric_name}' returned within policy threshold",
                )
            return None

        raise ValueError(f"unsupported regression rule kind: {rule.kind}")

    @staticmethod
    def _finding(
        kind: ComparisonKind,
        rule: RegressionRule,
        baseline: ComparableEvaluationResult | None,
        current: ComparableEvaluationResult,
        message: str,
    ) -> ComparisonFinding:
        return ComparisonFinding(
            kind=kind,
            rule_id=rule.rule_id,
            case_id=current.case_id,
            message=message,
            baseline_result_id=None if baseline is None else baseline.result_id,
            current_result_id=current.result_id,
        )
