from ai_multi_agent_platform.control_plane import build_openapi


def test_search_openapi_is_published() -> None:
    specification = build_openapi()
    assert "/api/v1/search" in specification["paths"]
    assert "SearchResult" in specification["components"]["schemas"]
    assert "SearchPage" in specification["components"]["schemas"]
    parameters = specification["paths"]["/api/v1/search"]["get"]["parameters"]
    assert {parameter["name"] for parameter in parameters} >= {
        "updated_after",
        "updated_before",
    }
