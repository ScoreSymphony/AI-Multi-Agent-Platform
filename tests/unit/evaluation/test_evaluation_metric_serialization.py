from __future__ import annotations

import json
from dataclasses import asdict

from ai_multi_agent_platform.evaluation import (
    ComparisonOperator,
    EvaluationCase,
    EvaluationObservation,
    EvaluationOutcome,
    MetricRule,
    MetricThresholdEvaluator,
)


def test_missing_metric_fails_without_persisting_non_json_nan() -> None:
    case = EvaluationCase(
        case_id="case.missing-metric",
        name="Missing metric",
        version="1",
        metric_rules=(
            MetricRule(
                rule_id="latency-required",
                metric_name="latency_ms",
                operator=ComparisonOperator.LTE,
                threshold=500.0,
                unit="ms",
            ),
        ),
    )

    result = MetricThresholdEvaluator().evaluate(
        evaluation_run_id="evaluation_run_missing_metric",
        case=case,
        observation=EvaluationObservation(metrics={}),
    )

    assert result.outcome is EvaluationOutcome.FAILED
    assert result.deterministic_pass is False
    assert result.metrics == ()
    json.dumps(asdict(result), allow_nan=False, default=str)
