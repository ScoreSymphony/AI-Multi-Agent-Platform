from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.control_plane import build_openapi


def _schemas(specification: dict[str, Any]) -> dict[str, Any]:
    components = specification["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return schemas


def _assert_typed_page(schemas: dict[str, Any], page_name: str, item_name: str) -> None:
    page = schemas[page_name]
    assert page["required"] == ["items", "next_cursor", "total", "limit"]
    assert page["properties"]["items"]["items"] == {
        "$ref": f"#/components/schemas/{item_name}"
    }
    assert page["properties"]["next_cursor"]["type"] == ["string", "null"]


def test_search_transport_contract_is_generated_from_canonical_query_and_response_schema() -> None:
    specification = build_openapi()
    schemas = _schemas(specification)

    query = schemas["SearchQueryParameters"]
    assert query["properties"]["mode"]["enum"] == [
        "exact",
        "keyword",
        "metadata",
        "semantic",
        "hybrid",
    ]
    assert query["properties"]["assignment_state"]["enum"] == ["assigned", "unassigned"]
    assert query["properties"]["blocked"]["type"] == "boolean"

    result = schemas["SearchResult"]
    assert set(result["required"]) == set(result["properties"])
    assert result["properties"]["access"]["enum"] == ["authorized"]
    assert schemas["SearchPage"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/SearchResult"
    }


def test_observability_timeline_contract_covers_events_and_telemetry() -> None:
    specification = build_openapi()
    schemas = _schemas(specification)

    assert schemas["TimelineItem"]["oneOf"] == [
        {"$ref": "#/components/schemas/CanonicalEvent"},
        {"$ref": "#/components/schemas/TelemetryTimelineEntry"},
    ]
    _assert_typed_page(schemas, "TimelinePage", "TimelineItem")

    event = schemas["CanonicalEvent"]
    assert set(event["required"]) == set(event["properties"])
    assert event["properties"]["owner_ref"]["oneOf"] == [
        {"$ref": "#/components/schemas/Owner"},
        {"type": "null"},
    ]
    assert event["properties"]["external_refs"]["items"]["required"] == [
        "system",
        "kind",
        "value",
    ]

    failure_components = [
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
    telemetry = schemas["TelemetryTimelineEntry"]
    assert telemetry["properties"]["component"]["enum"] == failure_components
    assert telemetry["properties"]["outcome"]["enum"] == [
        "unknown",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
    ]
    assert telemetry["properties"]["context"]["additionalProperties"] == {"type": "string"}
    assert telemetry["properties"]["duration_seconds"]["type"] == ["number", "null"]
    assert telemetry["properties"]["failure"]["oneOf"][-1] == {"type": "null"}
    failure = schemas["TelemetryFailure"]
    assert failure["required"] == ["component", "code", "retryable"]
    assert failure["properties"]["component"]["enum"] == failure_components

    timeline_response = specification["paths"]["/api/v1/tasks/{task_id}/timeline"]["get"][
        "responses"
    ]["200"]
    assert timeline_response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TimelinePage"
    }


def test_accounting_transport_contract_preserves_nullability_enums_and_typed_pages() -> None:
    specification = build_openapi(
        extension_collections=("usage-records", "usage-aggregates", "usage-budgets")
    )
    schemas = _schemas(specification)

    record = schemas["UsageRecord"]
    assert record["properties"]["quantity"]["type"] == ["number", "null"]
    assert record["properties"]["quality"]["enum"] == [
        "measured",
        "reported",
        "estimated",
        "unavailable",
    ]
    assert record["properties"]["aggregation_mode"]["enum"] == ["additive", "latest"]
    _assert_typed_page(schemas, "UsageRecordPage", "UsageRecord")

    aggregate = schemas["UsageAggregate"]
    assert aggregate["properties"]["quality_counts"] == {
        "$ref": "#/components/schemas/UsageQualityCounts"
    }
    assert aggregate["properties"]["trend"]["items"] == {
        "$ref": "#/components/schemas/UsageTrendPoint"
    }
    _assert_typed_page(schemas, "UsageAggregatePage", "UsageAggregate")

    budget = schemas["UsageBudget"]
    assert budget["properties"]["kind"]["enum"] == ["soft", "hard"]
    assert budget["properties"]["consumed"]["type"] == "number"
    assert budget["properties"]["remaining"]["type"] == "number"
    assert budget["properties"]["fraction"]["type"] == "number"
    assert budget["properties"]["threshold_level"]["enum"] == [
        "warning",
        "exceeded",
        None,
    ]
    _assert_typed_page(schemas, "UsageBudgetPage", "UsageBudget")

    for collection, page_schema in (
        ("usage-records", "UsageRecordPage"),
        ("usage-aggregates", "UsageAggregatePage"),
        ("usage-budgets", "UsageBudgetPage"),
    ):
        response = specification["paths"][f"/api/v1/{collection}"]["get"]["responses"]["200"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": f"#/components/schemas/{page_schema}"
        }


def test_generated_client_contract_marker_declares_completed_surface_coverage() -> None:
    marker = build_openapi()["x-generated-client-contract"]
    assert marker["covered_surfaces"] == [
        "foundation",
        "search",
        "observability-timeline",
        "accounting-usage",
    ]
