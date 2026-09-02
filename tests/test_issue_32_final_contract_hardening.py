from __future__ import annotations

import asyncio

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    CommandHandler,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _stack(
    *,
    command_handlers: dict[str, CommandHandler] | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        command_handlers=command_handlers,
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def test_api_errors_expose_stable_category_and_openapi_requires_it() -> None:
    async def scenario() -> None:
        _, http = _stack()
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={"idempotency-key": "missing-content-type"},
                body={"title": "Task"},
            )
        )
        assert response.status == 415
        assert isinstance(response.body, dict)
        assert response.body["code"] == "unsupported_media_type"
        assert response.body["category"] == "validation"

    asyncio.run(scenario())

    specification = build_openapi()
    api_error = specification["components"]["schemas"]["APIError"]
    assert "category" in api_error["required"]
    assert api_error["properties"]["category"] == {"type": "string"}

    task_responses = specification["paths"]["/api/v1/tasks"]["post"]["responses"]
    for status in ("401", "413", "422"):
        assert status in task_responses


def test_extension_openapi_matches_filter_and_command_request_contract() -> None:
    specification = build_openapi(
        extension_collections=("widgets",),
        extension_commands=("widget.refresh",),
    )

    widget_parameters = specification["paths"]["/api/v1/widgets"]["get"]["parameters"]
    assert any(parameter.get("name") == "filter[field]" for parameter in widget_parameters)

    command_operation = specification["paths"]["/api/v1/commands/{command}"]["post"]
    request_body = command_operation["requestBody"]
    schema = request_body["content"]["application/json"]["schema"]
    assert schema["$ref"] == "#/components/schemas/CanonicalExtensionCommandRequest"

    request_schema = specification["components"]["schemas"]["CanonicalExtensionCommandRequest"]
    assert request_schema["required"] == ["resource_ref"]
    assert request_schema["properties"]["resource_ref"]["minLength"] == 1

    for status in ("401", "413", "415", "422"):
        assert status in command_operation["responses"]


def test_extension_commands_require_json_content_type() -> None:
    async def scenario() -> None:
        async def refresh_widget(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, payload
            return {"id": resource_ref, "type": "widget", "refreshed": True}

        _, http = _stack(command_handlers={"widget.refresh": refresh_widget})
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/widget.refresh",
                headers={"idempotency-key": "refresh-no-json"},
                body={"resource_ref": "widget-test"},
            )
        )
        assert response.status == 415
        assert isinstance(response.body, dict)
        assert response.body["code"] == "unsupported_media_type"
        assert response.body["category"] == "validation"

    asyncio.run(scenario())


def test_extension_private_payload_rejection_is_recursive() -> None:
    async def scenario() -> None:
        async def leaking_command(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, payload
            return {
                "id": resource_ref,
                "type": "widget",
                "data": {"backend_ref": "private-executor-42"},
            }

        _, http = _stack(command_handlers={"widget.inspect": leaking_command})
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/widget.inspect",
                headers={
                    "content-type": "application/json",
                    "idempotency-key": "inspect-private-payload",
                },
                body={"resource_ref": "widget-test"},
            )
        )
        assert response.status == 500
        assert isinstance(response.body, dict)
        assert response.body["code"] == "contract_violation"
        assert response.body["category"] == "contract"
        assert response.body["details"] == {"fields": ["data.backend_ref"]}
        assert "private-executor-42" not in str(response.body)

    asyncio.run(scenario())
