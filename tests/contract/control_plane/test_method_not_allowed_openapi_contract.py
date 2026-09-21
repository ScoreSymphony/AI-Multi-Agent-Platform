from __future__ import annotations

from ai_multi_agent_platform.control_plane import (
    RELEASE_STATUS_PATH,
    build_openapi as build_public_openapi,
)
from ai_multi_agent_platform.control_plane.extensions import (
    build_openapi as build_extension_openapi,
)
from ai_multi_agent_platform.control_plane.openapi import (
    build_openapi as build_foundation_openapi,
)


ERROR_RESPONSE_REF = {"$ref": "#/components/responses/Error"}


def _assert_canonical_error_response(specification: dict[str, object]) -> None:
    components = specification["components"]
    assert isinstance(components, dict)
    responses = components["responses"]
    assert isinstance(responses, dict)
    error = responses["Error"]
    assert isinstance(error, dict)
    content = error["content"]
    assert isinstance(content, dict)
    application_json = content["application/json"]
    assert isinstance(application_json, dict)
    assert application_json["schema"] == {"$ref": "#/components/schemas/APIError"}


def test_foundation_openapi_documents_canonical_405_without_new_operations() -> None:
    specification = build_foundation_openapi()
    _assert_canonical_error_response(specification)

    paths = specification["paths"]
    assert isinstance(paths, dict)

    assert paths["/api/v1/openapi.json"]["get"]["responses"]["405"] == ERROR_RESPONSE_REF
    assert paths["/api/v1/projects"]["get"]["responses"]["405"] == ERROR_RESPONSE_REF
    assert paths["/api/v1/projects"]["post"]["responses"]["405"] == ERROR_RESPONSE_REF
    assert paths["/api/v1/tasks/{task_id}:queue"]["post"]["responses"]["405"] == ERROR_RESPONSE_REF

    assert set(paths["/api/v1/openapi.json"]) == {"get"}
    assert set(paths["/api/v1/projects"]) == {"get", "post"}
    assert set(paths["/api/v1/tasks/{task_id}:queue"]) == {"post"}


def test_registered_extension_openapi_documents_405_without_new_operations() -> None:
    specification = build_extension_openapi(
        extension_collections=("widgets",),
        extension_commands=("widget.refresh",),
    )
    _assert_canonical_error_response(specification)

    paths = specification["paths"]
    assert isinstance(paths, dict)

    assert paths["/api/v1/widgets"]["get"]["responses"]["405"] == ERROR_RESPONSE_REF
    assert (
        paths["/api/v1/widgets/{resource_id}"]["get"]["responses"]["405"]
        == ERROR_RESPONSE_REF
    )
    assert (
        paths["/api/v1/commands/{command}"]["post"]["responses"]["405"]
        == ERROR_RESPONSE_REF
    )

    assert set(paths["/api/v1/widgets"]) == {"get"}
    assert set(paths["/api/v1/widgets/{resource_id}"]) == {"get"}
    assert set(paths["/api/v1/commands/{command}"]) == {"post"}


def test_final_composed_openapi_normalizes_contributed_public_route_405() -> None:
    specification = build_public_openapi()
    _assert_canonical_error_response(specification)

    paths = specification["paths"]
    assert isinstance(paths, dict)
    release_status = paths[RELEASE_STATUS_PATH]

    assert set(release_status) == {"get"}
    assert release_status["get"]["responses"]["405"] == ERROR_RESPONSE_REF
