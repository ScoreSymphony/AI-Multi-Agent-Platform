"""Strict JSON codec for durable evaluation persistence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    AssertionResult,
    ComparisonFinding,
    ComparisonKind,
    ComparisonOperator,
    ComparisonReport,
    ConfigurationSnapshot,
    EvaluationOutcome,
    EvaluationResult,
    EvaluationRun,
    EvaluationRunStatus,
    EvaluatorDescriptor,
    EvaluatorKind,
    MetricResult,
    SnapshotValue,
    VersionReference,
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
        raise ValueError("stored evaluation payload must be a JSON object")
    return cast(dict[str, Any], parsed)


def _required_str(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise ValueError(f"stored evaluation field '{key}' must be a string")
    return value


def _optional_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"stored evaluation field '{key}' must be a string or null")
    return value


def _required_int(obj: dict[str, Any], key: str) -> int:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored evaluation field '{key}' must be an integer")
    return value


def _optional_int(obj: dict[str, Any], key: str) -> int | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"stored evaluation field '{key}' must be an integer or null")
    return value


def _optional_float(obj: dict[str, Any], key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"stored evaluation field '{key}' must be numeric or null")
    return float(value)


def _required_bool(obj: dict[str, Any], key: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"stored evaluation field '{key}' must be boolean")
    return value


def _optional_bool(obj: dict[str, Any], key: str) -> bool | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"stored evaluation field '{key}' must be boolean or null")
    return value


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("stored evaluation timestamp must be timezone-aware")
    return parsed


def _list(obj: dict[str, Any], key: str) -> list[Any]:
    value = obj.get(key)
    if not isinstance(value, list):
        raise ValueError(f"stored evaluation field '{key}' must be a list")
    return cast(list[Any], value)


def _dict(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"stored evaluation field '{field}' must be an object")
    return cast(dict[str, Any], value)


def _strings(obj: dict[str, Any], key: str) -> tuple[str, ...]:
    values = _list(obj, key)
    if any(not isinstance(value, str) for value in values):
        raise ValueError(f"stored evaluation field '{key}' must contain strings")
    return tuple(cast(str, value) for value in values)


def _snapshot_payload(snapshot: ConfigurationSnapshot) -> dict[str, object]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "schema_version": snapshot.schema_version,
        "platform_version": snapshot.platform_version,
        "platform_commit": snapshot.platform_commit,
        "references": [
            {
                "kind": ref.kind,
                "ref_id": ref.ref_id,
                "version": ref.version,
                "revision": ref.revision,
            }
            for ref in snapshot.references
        ],
        "environment": [{"key": item.key, "value": item.value} for item in snapshot.environment],
    }


def _decode_snapshot(obj: dict[str, Any]) -> ConfigurationSnapshot:
    references = tuple(
        VersionReference(
            kind=_required_str(ref, "kind"),
            ref_id=_required_str(ref, "ref_id"),
            version=_required_str(ref, "version"),
            revision=_optional_str(ref, "revision"),
        )
        for ref in (_dict(value, "references[]") for value in _list(obj, "references"))
    )
    environment = tuple(
        SnapshotValue(key=_required_str(item, "key"), value=_required_str(item, "value"))
        for item in (_dict(value, "environment[]") for value in _list(obj, "environment"))
    )
    return ConfigurationSnapshot(
        platform_version=_required_str(obj, "platform_version"),
        references=references,
        platform_commit=_optional_str(obj, "platform_commit"),
        environment=environment,
        snapshot_id=_required_str(obj, "snapshot_id"),
        schema_version=_required_str(obj, "schema_version"),
    )


def encode_run(run: EvaluationRun) -> str:
    return _dump(
        {
            "run_id": run.run_id,
            "suite_id": run.suite_id,
            "suite_version": run.suite_version,
            "status": run.status.value,
            "baseline_run_id": run.baseline_run_id,
            "repetitions": run.repetitions,
            "seed": run.seed,
            "started_at": run.started_at.isoformat(),
            "completed_at": None if run.completed_at is None else run.completed_at.isoformat(),
            "snapshot": _snapshot_payload(run.snapshot),
        }
    )


def decode_run(raw: str) -> EvaluationRun:
    obj = _load(raw)
    completed_at = _optional_str(obj, "completed_at")
    return EvaluationRun(
        suite_id=_required_str(obj, "suite_id"),
        suite_version=_required_str(obj, "suite_version"),
        snapshot=_decode_snapshot(_dict(obj.get("snapshot"), "snapshot")),
        status=EvaluationRunStatus(_required_str(obj, "status")),
        baseline_run_id=_optional_str(obj, "baseline_run_id"),
        repetitions=_required_int(obj, "repetitions"),
        seed=_optional_int(obj, "seed"),
        run_id=_required_str(obj, "run_id"),
        started_at=_datetime(_required_str(obj, "started_at")),
        completed_at=None if completed_at is None else _datetime(completed_at),
    )


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


def encode_result(result: EvaluationResult) -> str:
    return _dump(
        {
            "result_id": result.result_id,
            "evaluation_run_id": result.evaluation_run_id,
            "case_id": result.case_id,
            "case_version": result.case_version,
            "evaluator": _evaluator_payload(result.evaluator),
            "outcome": result.outcome.value,
            "deterministic_pass": result.deterministic_pass,
            "score": result.score,
            "assertions": [
                {
                    "assertion_id": item.assertion_id,
                    "passed": item.passed,
                    "message": item.message,
                    "expected": item.expected,
                    "actual": item.actual,
                }
                for item in result.assertions
            ],
            "metrics": [
                {
                    "metric_name": item.metric_name,
                    "value": item.value,
                    "passed": item.passed,
                    "threshold": item.threshold,
                    "operator": None if item.operator is None else item.operator.value,
                    "unit": item.unit,
                }
                for item in result.metrics
            ],
            "case_tags": list(result.case_tags),
            "task_id": result.task_id,
            "run_id": result.run_id,
            "artifact_refs": list(result.artifact_refs),
            "telemetry_refs": list(result.telemetry_refs),
            "attempt_id": result.attempt_id,
            "repetition_index": result.repetition_index,
            "seed": result.seed,
            "error_category": result.error_category,
            "error_message": result.error_message,
            "created_at": result.created_at.isoformat(),
        }
    )


def decode_result(raw: str) -> EvaluationResult:
    obj = _load(raw)
    assertions = tuple(
        AssertionResult(
            assertion_id=_required_str(item, "assertion_id"),
            passed=_required_bool(item, "passed"),
            message=_required_str(item, "message"),
            expected=cast(JsonValue, item.get("expected")),
            actual=cast(JsonValue, item.get("actual")),
        )
        for item in (_dict(value, "assertions[]") for value in _list(obj, "assertions"))
    )
    metrics: list[MetricResult] = []
    for value in _list(obj, "metrics"):
        item = _dict(value, "metrics[]")
        metric_value = _optional_float(item, "value")
        if metric_value is None:
            raise ValueError("stored observed metric value must not be null")
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
    return EvaluationResult(
        evaluation_run_id=_required_str(obj, "evaluation_run_id"),
        case_id=_required_str(obj, "case_id"),
        case_version=_required_str(obj, "case_version"),
        evaluator=_decode_evaluator(_dict(obj.get("evaluator"), "evaluator")),
        outcome=EvaluationOutcome(_required_str(obj, "outcome")),
        deterministic_pass=_optional_bool(obj, "deterministic_pass"),
        score=_optional_float(obj, "score"),
        assertions=assertions,
        metrics=tuple(metrics),
        case_tags=_strings(obj, "case_tags"),
        task_id=_optional_str(obj, "task_id"),
        run_id=_optional_str(obj, "run_id"),
        artifact_refs=_strings(obj, "artifact_refs"),
        telemetry_refs=_strings(obj, "telemetry_refs"),
        attempt_id=_optional_str(obj, "attempt_id"),
        repetition_index=_required_int(obj, "repetition_index"),
        seed=_optional_int(obj, "seed"),
        error_category=_optional_str(obj, "error_category"),
        error_message=_optional_str(obj, "error_message"),
        result_id=_required_str(obj, "result_id"),
        created_at=_datetime(_required_str(obj, "created_at")),
    )


def encode_comparison(report: ComparisonReport) -> str:
    return _dump(
        {
            "baseline_run_id": report.baseline_run_id,
            "current_run_id": report.current_run_id,
            "policy_id": report.policy_id,
            "policy_version": report.policy_version,
            "findings": [
                {
                    "kind": finding.kind.value,
                    "rule_id": finding.rule_id,
                    "case_id": finding.case_id,
                    "message": finding.message,
                    "baseline_result_id": finding.baseline_result_id,
                    "current_result_id": finding.current_result_id,
                }
                for finding in report.findings
            ],
        }
    )


def decode_comparison(raw: str) -> ComparisonReport:
    obj = _load(raw)
    findings = tuple(
        ComparisonFinding(
            kind=ComparisonKind(_required_str(item, "kind")),
            rule_id=_required_str(item, "rule_id"),
            case_id=_required_str(item, "case_id"),
            message=_required_str(item, "message"),
            baseline_result_id=_optional_str(item, "baseline_result_id"),
            current_result_id=_required_str(item, "current_result_id"),
        )
        for item in (_dict(value, "findings[]") for value in _list(obj, "findings"))
    )
    return ComparisonReport(
        baseline_run_id=_required_str(obj, "baseline_run_id"),
        current_run_id=_required_str(obj, "current_run_id"),
        policy_id=_required_str(obj, "policy_id"),
        policy_version=_required_str(obj, "policy_version"),
        findings=findings,
    )
