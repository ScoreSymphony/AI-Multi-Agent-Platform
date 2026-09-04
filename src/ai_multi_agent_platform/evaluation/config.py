"""Strict checked-in configuration loaders for canonical evaluation assets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .codec import decode_result, decode_run
from .models import (
    ComparisonOperator,
    DeterministicAssertion,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    MetricRule,
    RegressionPolicy,
    RegressionRule,
    RegressionRuleKind,
    RubricCriterion,
    SnapshotValue,
)

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class EvaluationBaseline:
    """One accepted canonical run plus the exact results used for comparison."""

    run: EvaluationRun
    results: tuple[EvaluationResult, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_object(path: str | Path, *, label: str) -> JsonObject:
    source = Path(path)
    try:
        parsed = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} must contain valid JSON: {source}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} root must be a JSON object: {source}")
    return cast(JsonObject, parsed)


def _object(value: Any, *, context: str) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError(f"{context} must be a JSON object")
    return cast(JsonObject, value)


def _list(value: Any, *, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a JSON array")
    return cast(list[Any], value)


def _keys(
    obj: Mapping[str, Any],
    *,
    context: str,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
) -> None:
    missing = sorted(required - obj.keys())
    if missing:
        raise ValueError(f"{context} is missing required fields: {', '.join(missing)}")
    unknown = sorted(obj.keys() - required - optional)
    if unknown:
        raise ValueError(f"{context} contains unknown fields: {', '.join(unknown)}")


def _string(obj: Mapping[str, Any], key: str, *, context: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be a non-blank string")
    return value


def _optional_string(obj: Mapping[str, Any], key: str, *, context: str) -> str | None:
    value = obj.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} must be null or a non-blank string")
    return value


def _number(obj: Mapping[str, Any], key: str, *, context: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{context}.{key} must be numeric")
    return float(value)


def _optional_number(obj: Mapping[str, Any], key: str, *, context: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{context}.{key} must be null or numeric")
    return float(value)


def _strings(value: Any, *, context: str) -> tuple[str, ...]:
    items = _list(value, context=context)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        raise ValueError(f"{context} must contain only non-blank strings")
    return tuple(cast(str, item) for item in items)


def _snapshot_values(value: Any, *, context: str) -> tuple[SnapshotValue, ...]:
    items: list[SnapshotValue] = []
    for index, raw in enumerate(_list(value, context=context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, context=item_context)
        _keys(
            item,
            context=item_context,
            required=frozenset({"key", "value"}),
        )
        items.append(
            SnapshotValue(
                key=_string(item, "key", context=item_context),
                value=_string(item, "value", context=item_context),
            )
        )
    return tuple(items)


def _assertions(value: Any, *, context: str) -> tuple[DeterministicAssertion, ...]:
    assertions: list[DeterministicAssertion] = []
    for index, raw in enumerate(_list(value, context=context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, context=item_context)
        _keys(
            item,
            context=item_context,
            required=frozenset({"assertion_id", "path", "operator"}),
            optional=frozenset({"expected", "message"}),
        )
        message = item.get("message", "")
        if not isinstance(message, str):
            raise ValueError(f"{item_context}.message must be a string")
        assertions.append(
            DeterministicAssertion(
                assertion_id=_string(item, "assertion_id", context=item_context),
                path=_string(item, "path", context=item_context),
                operator=ComparisonOperator(_string(item, "operator", context=item_context)),
                expected=cast(JsonValue, item.get("expected")),
                message=message,
            )
        )
    return tuple(assertions)


def _metric_rules(value: Any, *, context: str) -> tuple[MetricRule, ...]:
    rules: list[MetricRule] = []
    for index, raw in enumerate(_list(value, context=context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, context=item_context)
        _keys(
            item,
            context=item_context,
            required=frozenset({"rule_id", "metric_name", "operator", "threshold"}),
            optional=frozenset({"unit"}),
        )
        rules.append(
            MetricRule(
                rule_id=_string(item, "rule_id", context=item_context),
                metric_name=_string(item, "metric_name", context=item_context),
                operator=ComparisonOperator(_string(item, "operator", context=item_context)),
                threshold=_number(item, "threshold", context=item_context),
                unit=_optional_string(item, "unit", context=item_context),
            )
        )
    return tuple(rules)


def _rubric(value: Any, *, context: str) -> tuple[RubricCriterion, ...]:
    criteria: list[RubricCriterion] = []
    for index, raw in enumerate(_list(value, context=context)):
        item_context = f"{context}[{index}]"
        item = _object(raw, context=item_context)
        _keys(
            item,
            context=item_context,
            required=frozenset({"criterion_id", "description"}),
            optional=frozenset({"weight"}),
        )
        weight = item.get("weight", 1.0)
        if isinstance(weight, bool) or not isinstance(weight, int | float):
            raise ValueError(f"{item_context}.weight must be numeric")
        criteria.append(
            RubricCriterion(
                criterion_id=_string(item, "criterion_id", context=item_context),
                description=_string(item, "description", context=item_context),
                weight=float(weight),
            )
        )
    return tuple(criteria)


def parse_evaluation_suite(obj: Mapping[str, Any]) -> EvaluationSuite:
    """Parse one suite while rejecting unknown or malformed configuration fields."""

    _keys(
        obj,
        context="evaluation suite",
        required=frozenset({"suite_id", "name", "version", "cases"}),
        optional=frozenset({"description", "tags"}),
    )
    cases: list[EvaluationCase] = []
    for index, raw in enumerate(_list(obj.get("cases"), context="evaluation suite.cases")):
        context = f"evaluation suite.cases[{index}]"
        item = _object(raw, context=context)
        _keys(
            item,
            context=context,
            required=frozenset({"case_id", "name", "version"}),
            optional=frozenset(
                {
                    "input_template",
                    "fixtures",
                    "assertions",
                    "metric_rules",
                    "rubric",
                    "timeout_seconds",
                    "resource_limits",
                    "tags",
                    "category",
                    "difficulty",
                }
            ),
        )
        input_template = _object(
            item.get("input_template", {}), context=f"{context}.input_template"
        )
        cases.append(
            EvaluationCase(
                case_id=_string(item, "case_id", context=context),
                name=_string(item, "name", context=context),
                version=_string(item, "version", context=context),
                input_template=cast(dict[str, JsonValue], dict(input_template)),
                fixtures=_strings(item.get("fixtures", []), context=f"{context}.fixtures"),
                assertions=_assertions(item.get("assertions", []), context=f"{context}.assertions"),
                metric_rules=_metric_rules(
                    item.get("metric_rules", []), context=f"{context}.metric_rules"
                ),
                rubric=_rubric(item.get("rubric", []), context=f"{context}.rubric"),
                timeout_seconds=_optional_number(item, "timeout_seconds", context=context),
                resource_limits=_snapshot_values(
                    item.get("resource_limits", []), context=f"{context}.resource_limits"
                ),
                tags=_strings(item.get("tags", []), context=f"{context}.tags"),
                category=_optional_string(item, "category", context=context),
                difficulty=_optional_string(item, "difficulty", context=context),
            )
        )
    description = obj.get("description", "")
    if not isinstance(description, str):
        raise ValueError("evaluation suite.description must be a string")
    return EvaluationSuite(
        suite_id=_string(obj, "suite_id", context="evaluation suite"),
        name=_string(obj, "name", context="evaluation suite"),
        version=_string(obj, "version", context="evaluation suite"),
        cases=tuple(cases),
        description=description,
        tags=_strings(obj.get("tags", []), context="evaluation suite.tags"),
    )


def load_evaluation_suite(path: str | Path) -> EvaluationSuite:
    return parse_evaluation_suite(_load_object(path, label="evaluation suite"))


def parse_regression_policy(obj: Mapping[str, Any]) -> RegressionPolicy:
    """Parse one versioned regression policy from strict JSON data."""

    _keys(
        obj,
        context="regression policy",
        required=frozenset({"policy_id", "version", "rules"}),
    )
    rules: list[RegressionRule] = []
    for index, raw in enumerate(_list(obj.get("rules"), context="regression policy.rules")):
        context = f"regression policy.rules[{index}]"
        item = _object(raw, context=context)
        _keys(
            item,
            context=context,
            required=frozenset({"rule_id", "kind"}),
            optional=frozenset({"threshold", "metric_name", "metric_operator", "tag"}),
        )
        metric_operator = _optional_string(item, "metric_operator", context=context)
        rules.append(
            RegressionRule(
                rule_id=_string(item, "rule_id", context=context),
                kind=RegressionRuleKind(_string(item, "kind", context=context)),
                threshold=_optional_number(item, "threshold", context=context),
                metric_name=_optional_string(item, "metric_name", context=context),
                metric_operator=(
                    None if metric_operator is None else ComparisonOperator(metric_operator)
                ),
                tag=_optional_string(item, "tag", context=context),
            )
        )
    return RegressionPolicy(
        policy_id=_string(obj, "policy_id", context="regression policy"),
        version=_string(obj, "version", context="regression policy"),
        rules=tuple(rules),
    )


def load_regression_policy(path: str | Path) -> RegressionPolicy:
    return parse_regression_policy(_load_object(path, label="regression policy"))


def load_evaluation_baseline(
    path: str | Path,
    *,
    suite: EvaluationSuite | None = None,
) -> EvaluationBaseline:
    """Load a checked-in accepted run/result baseline through the durable canonical codec."""

    obj = _load_object(path, label="evaluation baseline")
    _keys(
        obj,
        context="evaluation baseline",
        required=frozenset({"run", "results"}),
    )
    run_obj = _object(obj.get("run"), context="evaluation baseline.run")
    run = decode_run(json.dumps(run_obj, allow_nan=False))
    results = tuple(
        decode_result(
            json.dumps(
                _object(raw, context=f"evaluation baseline.results[{index}]"), allow_nan=False
            )
        )
        for index, raw in enumerate(
            _list(obj.get("results"), context="evaluation baseline.results")
        )
    )
    if any(result.evaluation_run_id != run.run_id for result in results):
        raise ValueError("evaluation baseline results must reference the baseline run ID")
    identities = [(result.case_id, result.evaluator.evaluator_id) for result in results]
    if len(identities) != len(set(identities)):
        raise ValueError("evaluation baseline results must be unique by case/evaluator identity")
    if suite is not None:
        if run.suite_id != suite.suite_id or run.suite_version != suite.version:
            raise ValueError("evaluation baseline suite identity/version does not match the suite")
        case_versions = {case.case_id: case.version for case in suite.cases}
        for result in results:
            expected_version = case_versions.get(result.case_id)
            if expected_version is None or result.case_version != expected_version:
                raise ValueError(
                    "evaluation baseline result case identity/version does not match the suite"
                )
    return EvaluationBaseline(run=run, results=results)


__all__ = [
    "EvaluationBaseline",
    "load_evaluation_baseline",
    "load_evaluation_suite",
    "load_regression_policy",
    "parse_evaluation_suite",
    "parse_regression_policy",
]
