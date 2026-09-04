"""Strict checked-in configuration loader for stochastic aggregation policies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .aggregation import AggregationMethod, AggregationPolicy

JsonObject = dict[str, Any]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _load_object(path: str | Path) -> JsonObject:
    source = Path(path)
    try:
        parsed = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"aggregation policy must contain valid JSON: {source}") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"aggregation policy root must be a JSON object: {source}")
    return cast(JsonObject, parsed)


def _required_string(obj: Mapping[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"aggregation policy.{key} must be a non-blank string")
    return value


def _required_number(obj: Mapping[str, Any], key: str) -> float:
    value = obj.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"aggregation policy.{key} must be numeric")
    return float(value)


def _required_bool(obj: Mapping[str, Any], key: str) -> bool:
    value = obj.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"aggregation policy.{key} must be boolean")
    return value


def parse_aggregation_policy(obj: Mapping[str, Any]) -> AggregationPolicy:
    """Parse one explicit policy while rejecting missing or unknown configuration."""

    required = {
        "policy_id",
        "version",
        "score_method",
        "metric_method",
        "minimum_pass_rate",
        "fail_on_error",
        "require_equal_sample_count",
    }
    missing = sorted(required - obj.keys())
    if missing:
        raise ValueError(
            "aggregation policy is missing required fields: " + ", ".join(missing)
        )
    unknown = sorted(obj.keys() - required)
    if unknown:
        raise ValueError("aggregation policy contains unknown fields: " + ", ".join(unknown))
    return AggregationPolicy(
        policy_id=_required_string(obj, "policy_id"),
        version=_required_string(obj, "version"),
        score_method=AggregationMethod(_required_string(obj, "score_method")),
        metric_method=AggregationMethod(_required_string(obj, "metric_method")),
        minimum_pass_rate=_required_number(obj, "minimum_pass_rate"),
        fail_on_error=_required_bool(obj, "fail_on_error"),
        require_equal_sample_count=_required_bool(obj, "require_equal_sample_count"),
    )


def load_aggregation_policy(path: str | Path) -> AggregationPolicy:
    return parse_aggregation_policy(_load_object(path))


__all__ = ["load_aggregation_policy", "parse_aggregation_policy"]
