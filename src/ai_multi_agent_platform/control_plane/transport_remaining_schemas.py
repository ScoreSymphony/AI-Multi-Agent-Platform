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
_TELEMETRY_OUTCOME = ["unknown", "succeeded", "failed", "cancelled", "timed_out"]
_FAILURE_COMPONENT = [
    "domain_kernel",
    "orchestration",
    "agent",
    "execution",
    "model_provider_router",
    "capability_tool",
    "persistence_storage",
    "authorization_approval",
    "verification",
    "scheduler_worker_node",
    "automation",
    "control_plane_ha",
    "connector_browser",
    "plugin_adapter",
    "infrastructure_unknown",
]


def augment_remaining_transport_schemas(specification: dict[str, Any]) -> dict[str, Any]:
    """Complete generated-client coverage for Search, timeline telemetry and accounting."""

    schemas = specification.setdefault("components", {}).setdefault("schemas", {})
    _complete_search_schemas(specification, schemas)
    page_schema = schemas.get("Page")
    if not isinstance(page_schema, dict):
        raise RuntimeError("canonical OpenAPI is missing the Page schema")
    schemas.update(_telemetry_schemas(page_schema))
    schemas.update(_accounting_schemas(page_schema))

    _bind_response_schema(
        specification,
        f"/api/{API_VERSION}/search",
        "get",
        "200",
        "SearchPage",
    )
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
    paths = specification.get("paths")
    path = paths.get(f"/api/{API_VERSION}/search") if isinstance(paths, dict) else None
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


def _external_ref_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["system", "kind", "value"],
        "properties": {
            "system": {"type": "string"},
            "kind": {"type": "string"},
            "value": {"type": "string"},
        },
        "additionalProperties": False,
    }


def _provenance_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["source", "actor_ref", "details"],
        "properties": {
            "source": {"type": "string"},
            "actor_ref": _NULLABLE_STRING,
            "details": _JSON_OBJECT,
        },
        "additionalProperties": False,
    }


def _canonical_event_schema() -> dict[str, Any]:
    properties = {
        "id": {"type": "string"},
        "type": {"type": "string", "const": "event"},
        "event_type": {"type": "string"},
        "subject_type": {"type": "string"},
        "subject_id": {"type": "string"},
        "correlation_id": {"type": "string"},
        "owner_ref": {
            "oneOf": [
                {"$ref": "#/components/schemas/Owner"},
                {"type": "null"},
            ]
        },
        "project_id": _NULLABLE_STRING,
        "causation_id": _NULLABLE_STRING,
        "trace_id": _NULLABLE_STRING,
        "occurred_at": {"type": "string", "format": "date-time"},
        "payload": _JSON_OBJECT,
        "schema_version": {"type": "string"},
        "provenance": {"oneOf": [_provenance_schema(), {"type": "null"}]},
        "external_refs": {"type": "array", "items": _external_ref_schema()},
    }
    return _closed_required_object(properties)


def _telemetry_failure_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["component", "code", "retryable"],
        "properties": {
            "component": {"type": "string", "enum": _FAILURE_COMPONENT},
            "code": {"type": "string"},
            "retryable": {"type": "boolean"},
        },
        "additionalProperties": False,
    }


def _telemetry_timeline_entry_schema() -> dict[str, Any]:
    properties = {
        "id": {"type": "string"},
        "type": {"type": "string", "const": "telemetry"},
        "event_name": {"type": "string"},
        "component": {"type": "string", "enum": _FAILURE_COMPONENT},
        "timestamp": {"type": "string", "format": "date-time"},
        "outcome": {"type": "string", "enum": _TELEMETRY_OUTCOME},
        "duration_seconds": {"type": ["number", "null"]},
        "failure": {
            "oneOf": [
                {"$ref": "#/components/schemas/TelemetryFailure"},
                {"type": "null"},
            ]
        },
        "context": _STRING_MAP,
        "attributes": _JSON_OBJECT,
    }
    return _closed_required_object(properties)


def _telemetry_schemas(page_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "CanonicalEvent": _canonical_event_schema(),
        "TelemetryFailure": _telemetry_failure_schema(),
        "TelemetryTimelineEntry": _telemetry_timeline_entry_schema(),
        "TimelineItem": {
            "oneOf": [
                {"$ref": "#/components/schemas/CanonicalEvent"},
                {"$ref": "#/components/schemas/TelemetryTimelineEntry"},
            ]
        },
        "TimelinePage": _page_schema(page_schema, "TimelineItem"),
    }


def _quality_counts_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": _MEASUREMENT_QUALITY,
        "properties": {name: {"type": "integer", "minimum": 0} for name in _MEASUREMENT_QUALITY},
        "additionalProperties": False,
    }


def _accounting_schemas(page_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "UsageQualityCounts": _quality_counts_schema(),
        "UsageRecord": _usage_record_schema(),
        "UsageTrendPoint": _usage_trend_point_schema(),
        "UsageAggregate": _usage_aggregate_schema(),
        "UsageBudget": _usage_budget_schema(),
        "UsageRecordPage": _page_schema(page_schema, "UsageRecord"),
        "UsageAggregatePage": _page_schema(page_schema, "UsageAggregate"),
        "UsageBudgetPage": _page_schema(page_schema, "UsageBudget"),
    }


def _usage_record_schema() -> dict[str, Any]:
    properties = {
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
    }
    return _closed_required_object(properties)


def _usage_trend_point_schema() -> dict[str, Any]:
    properties = {
        "start": {"type": "string", "format": "date-time"},
        "end": {"type": "string", "format": "date-time"},
        "value": {"type": ["number", "null"]},
        "record_count": {"type": "integer", "minimum": 0},
        "unavailable_count": {"type": "integer", "minimum": 0},
        "quality_counts": {"$ref": "#/components/schemas/UsageQualityCounts"},
    }
    return _closed_required_object(properties)


def _usage_aggregate_schema() -> dict[str, Any]:
    properties = {
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
    }
    return _closed_required_object(properties)


def _usage_budget_schema() -> dict[str, Any]:
    properties = {
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
        "consumed": {"type": "number"},
        "remaining": {"type": "number"},
        "fraction": {"type": "number", "minimum": 0},
        "threshold_level": {
            "type": ["string", "null"],
            "enum": ["warning", "exceeded", None],
        },
    }
    return _closed_required_object(properties)


def _closed_required_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": list(properties),
        "properties": properties,
        "additionalProperties": False,
    }


def _page_schema(base_page: dict[str, Any], item_schema: str) -> dict[str, Any]:
    page = deepcopy(base_page)
    properties = page.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("canonical Page schema has no properties")
    properties["items"] = {
        "type": "array",
        "items": {"$ref": f"#/components/schemas/{item_schema}"},
    }
    return page


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
