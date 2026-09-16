from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.control_plane import build_openapi


def _schemas(specification: dict[str, Any]) -> dict[str, Any]:
    components = specification["components"]
    assert isinstance(components, dict)
    schemas = components["schemas"]
    assert isinstance(schemas, dict)
    return schemas


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
    telemetry = schemas["TelemetryTimelineEntry"]
    assert telemetry["properties"]["duration_seconds"]["type"] == ["number", "null"]
    assert telemetry["properties"]["failure"]["oneOf"][-1] == {"type": "null"}

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

    aggregate = schemas["UsageAggregate"]
    assert aggregate["properties"]["quality_counts"] == {
        "$ref": "#/components/schemas/UsageQualityCounts"
    }
    assert aggregate["properties"]["trend"]["items"] == {
        "$ref": "#/components/schemas/UsageTrendPoint"
    }

    budget = schemas["UsageBudget"]
    assert budget["properties"]["kind"]["enum"] == ["soft", "hard"]
    assert budget["properties"]["threshold_level"]["enum"] == [
        "warning",
        "exceeded",
        None,
    ]

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
