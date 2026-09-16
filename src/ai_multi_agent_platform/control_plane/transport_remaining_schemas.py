"""Remaining stable v1 transport schemas for generated frontend DTO coverage.

Search query metadata is derived from the already-canonical OpenAPI operation so the
frontend generator does not establish a second request contract. Telemetry and accounting
schemas describe the public Control Plane projections emitted by their runtime owners.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .models import API_VERSION

_NULLABLE_STRING = {"type": ["string", "null"]}
_TIMESTAMP_OR_NULL = {"type": ["string", "null"], "format": "date-time"}
_JSON_OBJECT = {"type": "object", "additionalProperties": True}
_STRING_MAP = {"type": "object", "additionalProperties": {"type": "string"}}
_MEASUREMENT_QUALITY = ["measured", "reported", "estimated", "unavailable"]
_AGGREGATION_MODE = ["additive", "latest"]


def augment_remaining_transport_schemas(specification: dict[str, Any]) -> dict[str, Any]:
    """Complete generated-client coverage for Search, timeline telemetry and accounting."""

    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    _complete_search_schemas(specification, schemas)
    schemas.update(_telemetry_schemas())
    schemas.update(_accounting_schemas())

    _bind_response_schema(specification, f"/api/{API_VERSION}/search", "get", "200", "SearchPage")
    _bind_response_schema(
        specification,
        f"/api/{API_VERSION}/tasks/{{task_id}}/timeline",
        "get",
        "200",
        "TimelinePage",
    )
    for collection, schema_name in (
        ("usage-records", "UsageRecordPage"),
        ("usage-aggregates", "UsageAggregatePage"),
        ("usage-budgets", "UsageBudgetPage"),
    ):
        _bind_response_schema(
            specification,
            f"/api/{API_VERSION}/{collection}",
            "get",
            "200",
            schema_name,
        )

    marker = specification.get("x-generated-client-contract")
    if isinstance(marker, dict):
        marker["covered_surfaces"] = [
            "foundation",
            "search",
            "observability-timeline",
            "accounting-usage",
        ]
    return specification


def _complete_search_schemas(
    specification: dict[str, Any],
    schemas: dict[str, Any],
) -> None:
    path = specification.get("paths", {}).get(f"/api/{API_VERSION}/search")
    operation = path.get("get") if isinstance(path, dict) else None
    parameters = operation.get("parameters") if isinstance(operation, dict) else None
    properties: dict[str, Any] = {}
    required: list[str] = []
    if isinstance(parameters, list):
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = parameter.get("name")
            schema = parameter.get("schema")
            if isinstance(name, str) and isinstance(schema, dict):
                properties[name] = deepcopy(schema)
                if parameter.get("required") is True:
                    required.append(name)
    if properties:
        query_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            query_schema["required"] = required
        schemas["SearchQueryParameters"] = query_schema

    search_result = schemas.get("SearchResult")
    if isinstance(search_result, dict):
        result_properties = search_result.get("properties")
        if isinstance(result_properties, dict):
            # SearchResult.to_json() emits every declared field on every result.
            search_result["required"] = list(result_properties)
            search_result["additionalProperties"] = False


def _telemetry_schemas() -> dict[str, Any]:
    return {
        "CanonicalEvent": {
            "type": "object",
            "required": [
                "id",
                "type",
                "schema_version",
                "event_type",
                "occurred_at",
                "subject_type",
                "subject_id",
                "correlation_id",
                "payload",
            ],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string", "const": "event"},
                "schema_version": {"type": "string"},
                "event_type": {"type": "string"},
                "occurred_at": {"type": "string", "format": "date-time"},
                "subject_type": {"type": "string"},
                "subject_id": {"type": "string"},
                "project_id": _NULLABLE_STRING,
                "correlation_id": {"type": "string"},
                "causation_id": _NULLABLE_STRING,
                "trace_id": _NULLABLE_STRING,
                "payload": _JSON_OBJECT,
            },
            # Canonical events may expose additive owner/provenance fields from the
            # versioned domain schema without changing timeline discrimination.
            "additionalProperties": True,
        },
        "TelemetryFailure": {
            "type": "object",
            "required": ["component", "code", "retryable"],
            "properties": {
                "component": {"type": "string"},
                "code": {"type": "string"},
                "retryable": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
        "TelemetryTimelineEntry": {
            "type": "object",
            "required": [
                "id",
                "type",
                "event_name",
                "component",
                "timestamp",
                "outcome",
                "duration_seconds",
                "failure",
                "context",
                "attributes",
            ],
            "properties": {
                "id": {"type": "string"},
                "type": {"type": "string", "const": "telemetry"},
                "event_name": {"type": "string"},
                "component": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"},
                "outcome": {"type": "string"},
                "duration_seconds": {"type": ["number", "null"]},
                "failure": {
                    "oneOf": [
                        {"$ref": "#/components/schemas/TelemetryFailure"},
                        {"type": "null"},
                    ]
                },
                "context": _JSON_OBJECT,
                "attributes": _JSON_OBJECT,
            },
            "additionalProperties": False,
        },
        "TimelineItem": {
            "oneOf": [
                {"$ref": "#/components/schemas/CanonicalEvent"},
                {"$ref": "#/components/schemas/TelemetryTimelineEntry"},
            ]
        },
        "TimelinePage": _page_schema("TimelineItem"),
    }


def _accounting_schemas() -> dict[str, Any]:
    quality_counts = {
        "type": "object",
        "required": _MEASUREMENT_QUALITY,
        "properties": {
            name: {"type": "integer", "minimum": 0} for name in _MEASUREMENT_QUALITY
        },
        "additionalProperties": False,
    }
    usage_record = {
        "type": "object",
        "required": [
            "id",
            "type",
            "metric_type",
            "quantity",
            "unit",
            "quality",
            "aggregation_mode",
            "source",
            "provider",
            "timestamp",
            "started_at",
            "ended_at",
            "scope",
            "correlation_id",
            "causation_id",
            "cost_amount",
            "currency",
            "precision",
            "confidence",
            "provenance",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "usage-record"},
            "metric_type": {"type": "string"},
            "quantity": {"type": ["number", "null"]},
            "unit": {"type": "string"},
            "quality": {"type": "string", "enum": _MEASUREMENT_QUALITY},
            "aggregation_mode": {"type": "string", "enum": _AGGREGATION_MODE},
            "source": {"type": "string"},
            "provider": _NULLABLE_STRING,
            "timestamp": {"type": "string", "format": "date-time"},
            "started_at": _TIMESTAMP_OR_NULL,
            "ended_at": _TIMESTAMP_OR_NULL,
            "scope": _STRING_MAP,
            "correlation_id": _NULLABLE_STRING,
            "causation_id": _NULLABLE_STRING,
            "cost_amount": {"type": ["number", "null"]},
            "currency": _NULLABLE_STRING,
            "precision": {"type": ["number", "null"]},
            "confidence": {"type": ["number", "null"]},
            "provenance": _JSON_OBJECT,
        },
        "additionalProperties": False,
    }
    trend_point = {
        "type": "object",
        "required": [
            "start",
            "end",
            "value",
            "record_count",
            "unavailable_count",
            "quality_counts",
        ],
        "properties": {
            "start": {"type": "string", "format": "date-time"},
            "end": {"type": "string", "format": "date-time"},
            "value": {"type": ["number", "null"]},
            "record_count": {"type": "integer", "minimum": 0},
            "unavailable_count": {"type": "integer", "minimum": 0},
            "quality_counts": {"$ref": "#/components/schemas/UsageQualityCounts"},
        },
        "additionalProperties": False,
    }
    usage_aggregate = {
        "type": "object",
        "required": [
            "id",
            "type",
            "metric_type",
            "unit",
            "total",
            "record_count",
            "unavailable_count",
            "quality_counts",
            "aggregation_mode",
            "scope",
            "trend_window_start",
            "trend_window_end",
            "trend_bucket_seconds",
            "trend",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "usage-aggregate"},
            "metric_type": {"type": "string"},
            "unit": {"type": "string"},
            "total": {"type": ["number", "null"]},
            "record_count": {"type": "integer", "minimum": 0},
            "unavailable_count": {"type": "integer", "minimum": 0},
            "quality_counts": {"$ref": "#/components/schemas/UsageQualityCounts"},
            "aggregation_mode": {"type": "string", "enum": _AGGREGATION_MODE},
            "scope": _STRING_MAP,
            "trend_window_start": _TIMESTAMP_OR_NULL,
            "trend_window_end": _TIMESTAMP_OR_NULL,
            "trend_bucket_seconds": {"type": ["integer", "null"]},
            "trend": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/UsageTrendPoint"},
            },
        },
        "additionalProperties": False,
    }
    usage_budget = {
        "type": "object",
        "required": [
            "id",
            "type",
            "metric_type",
            "unit",
            "scope_type",
            "scope_id",
            "limit",
            "kind",
            "action",
            "warning_fraction",
            "window_seconds",
            "window_mode",
            "window_start",
            "window_end",
            "include_estimated",
            "owner_type",
            "owner_id",
            "version",
            "consumed",
            "remaining",
            "fraction",
            "threshold_level",
        ],
        "properties": {
            "id": {"type": "string"},
            "type": {"type": "string", "const": "usage-budget"},
            "metric_type": {"type": "string"},
            "unit": {"type": "string"},
            "scope_type": {"type": "string"},
            "scope_id": {"type": "string"},
            "limit": {"type": "number", "exclusiveMinimum": 0},
            "kind": {"type": "string", "enum": ["soft", "hard"]},
            "action": {
                "type": "string",
                "enum": ["record_only", "warn", "deny", "require_approval", "notify"],
            },
            "warning_fraction": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "window_seconds": {"type": ["integer", "null"]},
            "window_mode": {"type": "string", "enum": ["lifetime", "rolling"]},
            "window_start": _TIMESTAMP_OR_NULL,
            "window_end": _TIMESTAMP_OR_NULL,
            "include_estimated": {"type": "boolean"},
            "owner_type": _NULLABLE_STRING,
            "owner_id": _NULLABLE_STRING,
            "version": {"type": "integer", "minimum": 1},
            "consumed": {"type": ["number", "null"]},
            "remaining": {"type": ["number", "null"]},
            "fraction": {"type": ["number", "null"]},
            "threshold_level": {
                "type": ["string", "null"],
                "enum": ["warning", "exceeded", None],
            },
        },
        "additionalProperties": False,
    }
    return {
        "UsageQualityCounts": quality_counts,
        "UsageRecord": usage_record,
        "UsageTrendPoint": trend_point,
        "UsageAggregate": usage_aggregate,
        "UsageBudget": usage_budget,
        "UsageRecordPage": _page_schema("UsageRecord"),
        "UsageAggregatePage": _page_schema("UsageAggregate"),
        "UsageBudgetPage": _page_schema("UsageBudget"),
    }


def _page_schema(item_schema: str) -> dict[str, Any]:
    return {
        "allOf": [
            {"$ref": "#/components/schemas/Page"},
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"$ref": f"#/components/schemas/{item_schema}"},
                    }
                },
            },
        ]
    }


def _bind_response_schema(
    specification: dict[str, Any],
    path: str,
    method: str,
    status: str,
    schema_name: str,
) -> None:
    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return
    path_item = paths.get(path)
    if not isinstance(path_item, dict):
        return
    operation = path_item.get(method)
    if not isinstance(operation, dict):
        return
    responses = operation.get("responses")
    if not isinstance(responses, dict):
        return
    response = responses.get(status)
    if not isinstance(response, dict):
        return
    response["content"] = {
        "application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}
    }
