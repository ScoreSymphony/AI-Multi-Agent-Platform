"""Strict JSON codec for derived stochastic evaluation aggregates."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from .aggregation import AggregatedEvaluationResult, AggregationSampleRef
from .models import (
    ComparisonOperator,
    EvaluationOutcome,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
)


def _dump(payload: dict[str, object]) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("stored evaluation aggregate must be a JSON object")
    return cast(dict[str, Any], parsed)


def _required_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be a string")
    return value


def _optional_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be a string or null")
    return value


def _required_int(obj: dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be an integer")
    return value


def _optional_int(obj: dict[str, Any], key: str) -> int | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be an integer or null")
    return value


def _optional_float(obj: dict[str, Any], key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be numeric or null")
    return float(value)


def _optional_bool(obj: dict[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be boolean or null")
    return value


def _required_bool(obj: dict[str, Any], key: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be boolean")
    return value


def _list(obj: dict[str, Any], key: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        raise ValueError(f"stored evaluation aggregate field '{key}' must be a list")
    return value


def _dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"stored evaluation aggregate field '{field}' must be an object")
    return cast(dict[str, Any], value)


def _strings(obj: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _list(obj, key)
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"stored evaluation aggregate field '{key}' must contain strings")
    return tuple(cast(str, value) for value in values)


def _evaluator_payload(descriptor: EvaluatorDescriptor) -> dict[str, object]:
    return {
        "evaluator_id": descriptor.evaluator_id,
        "kind": descriptor.kind.value,
        "version": descriptor.version,
        "deterministic": descriptor.deterministic,
        "model_config_id": descriptor.model_config_id,
        "provider_id": descriptor.provider_id,
        "configuration_ref": descriptor.configuration_ref,
    }


def _decode_evaluator(obj: dict[str, Any]) -> EvaluatorDescriptor:
    return EvaluatorDescriptor(
        evaluator_id=_required_str(obj, "evaluator_id"),
        kind=EvaluatorKind(_required_str(obj, "kind")),
        version=_required_str(obj, "version"),
        deterministic=_required_bool(obj, "deterministic"),
        model_config_id=_optional_str(obj, "model_config_id"),
        provider_id=_optional_str(obj, "provider_id"),
        configuration_ref=_optional_str(obj, "configuration_ref"),
    )


def encode_aggregate(aggregate: AggregatedEvaluationResult) -> str:
    created_at = aggregate.created_at
    if not isinstance(created_at, datetime):
        raise TypeError("evaluation aggregate created_at must be a datetime")
    return _dump(
        {
            "result_id": aggregate.result_id,
            "evaluation_run_id": aggregate.evaluation_run_id,
            "case_id": aggregate.case_id,
            "case_version": aggregate.case_version,
            "evaluator": _evaluator_payload(aggregate.evaluator),
            "outcome": aggregate.outcome.value,
            "deterministic_pass": aggregate.deterministic_pass,
            "score": aggregate.score,
            "metrics": [
                {
                    "metric_name": item.metric_name,
                    "value": item.value,
                    "passed": item.passed,
                    "threshold": item.threshold,
                    "operator": None if item.operator is None else item.operator.value,
                    "unit": item.unit,
                }
                for item in aggregate.metrics
            ],
            "case_tags": list(aggregate.case_tags),
            "aggregation_policy_id": aggregate.aggregation_policy_id,
            "aggregation_policy_version": aggregate.aggregation_policy_version,
            "samples": [
                {
                    "result_id": sample.result_id,
                    "repetition_index": sample.repetition_index,
                    "seed": sample.seed,
                    "outcome": sample.outcome.value,
                }
                for sample in aggregate.samples
            ],
            "task_ids": list(aggregate.task_ids),
            "run_ids": list(aggregate.run_ids),
            "artifact_refs": list(aggregate.artifact_refs),
            "telemetry_refs": list(aggregate.telemetry_refs),
            "created_at": created_at.isoformat(),
        }
    )


def decode_aggregate(raw: str) -> AggregatedEvaluationResult:
    obj = _load(raw)
    metrics: list[MetricResult] = []
    for value in _list(obj, "metrics"):
        item = _dict(value, "metrics[]")
        metric_value = _optional_float(item, "value")
        if metric_value is None:
            raise ValueError("stored aggregate metric value must not be null")
        operator = _optional_str(item, "operator")
        metrics.append(
            MetricResult(
                metric_name=_required_str(item, "metric_name"),
                value=metric_value,
                passed=_optional_bool(item, "passed"),
                threshold=_optional_float(item, "threshold"),
                operator=None if operator is None else ComparisonOperator(operator),
                unit=_optional_str(item, "unit"),
            )
        )
    samples = tuple(
        AggregationSampleRef(
            result_id=_required_str(item, "result_id"),
            repetition_index=_required_int(item, "repetition_index"),
            seed=_optional_int(item, "seed"),
            outcome=EvaluationOutcome(_required_str(item, "outcome")),
        )
        for item in (_dict(value, "samples[]") for value in _list(obj, "samples"))
    )
    created_at = datetime.fromisoformat(_required_str(obj, "created_at"))
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("stored evaluation aggregate timestamp must be timezone-aware")
    return AggregatedEvaluationResult(
        evaluation_run_id=_required_str(obj, "evaluation_run_id"),
        case_id=_required_str(obj, "case_id"),
        case_version=_required_str(obj, "case_version"),
        evaluator=_decode_evaluator(_dict(obj.get("evaluator"), "evaluator")),
        outcome=EvaluationOutcome(_required_str(obj, "outcome")),
        deterministic_pass=_optional_bool(obj, "deterministic_pass"),
        score=_optional_float(obj, "score"),
        metrics=tuple(metrics),
        case_tags=_strings(obj, "case_tags"),
        aggregation_policy_id=_required_str(obj, "aggregation_policy_id"),
        aggregation_policy_version=_required_str(obj, "aggregation_policy_version"),
        samples=samples,
        task_ids=_strings(obj, "task_ids"),
        run_ids=_strings(obj, "run_ids"),
        artifact_refs=_strings(obj, "artifact_refs"),
        telemetry_refs=_strings(obj, "telemetry_refs"),
        result_id=_required_str(obj, "result_id"),
        created_at=created_at,
    )


__all__ = ["decode_aggregate", "encode_aggregate"]
