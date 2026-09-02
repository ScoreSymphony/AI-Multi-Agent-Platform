from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    CURRENT_COLLECTIONS,
    FOUNDATION_COLLECTIONS,
    IMPLEMENTED_DOMAIN_COLLECTIONS,
    PLATFORM_COLLECTIONS,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    InMemoryResourceService,
    RequestContext,
    build_openapi,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


def _stack(
    *,
    authorization: FakeAuthorizationProvider | None = None,
    resource_services: dict[str, InMemoryResourceService] | None = None,
    command_handlers: dict[str, Any] | None = None,
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
        authorization=authorization,
        resource_services=resource_services,
        command_handlers=command_handlers,
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "X-Request-Id": "request-extension",
        "X-Correlation-Id": "correlation-extension",
        "X-Principal-Ref": "user:test",
        "X-Owner-Type": "user",
        "X-Owner-Id": "test",
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _assert_page(body: object, *, total: int) -> list[object]:
    assert isinstance(body, dict)
    assert body["total"] == total
    items = body["items"]
    assert isinstance(items, list)
    return items


def test_issue_32_foundation_is_separate_from_later_implemented_domains() -> None:
    assert FOUNDATION_COLLECTIONS == (
        "projects",
        "workspaces",
        "tasks",
        "plans",
        "steps",
        "runs",
        "artifacts",
        "results",
    )
    assert IMPLEMENTED_DOMAIN_COLLECTIONS == ("model-providers", "models")
    assert PLATFORM_COLLECTIONS == FOUNDATION_COLLECTIONS + IMPLEMENTED_DOMAIN_COLLECTIONS
    assert CURRENT_COLLECTIONS == PLATFORM_COLLECTIONS


def test_manifest_and_openapi_do_not_predeclare_unimplemented_future_domains() -> None:
    async def scenario() -> None:
        _, http = _stack()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert manifest.body["resources"] == list(PLATFORM_COLLECTIONS) + ["timeline"]
        assert manifest.body["commands"] == []

        specification = build_openapi()
        paths = specification["paths"]
        assert isinstance(paths, dict)
        for collection in PLATFORM_COLLECTIONS:
            assert f"/api/v1/{collection}" in paths
        for speculative_collection in (
            "agents",
            "teams",
            "files",
            "memory",
            "knowledge",
            "providers",
            "tools",
            "capabilities",
            "nodes",
            "workers",
            "approvals",
            "automations",
            "evaluations",
            "plugins",
            "adapters",
        ):
            assert f"/api/v1/{speculative_collection}" not in paths
        assert "/api/v1/commands/{command}" not in paths
        assert specification["x-control-plane-foundation-collections"] == list(
            FOUNDATION_COLLECTIONS
        )
        assert specification["x-implemented-domain-collections"] == list(
            IMPLEMENTED_DOMAIN_COLLECTIONS
        )
        assert specification["x-registered-extension-collections"] == []
        assert specification["x-registered-extension-commands"] == []

    asyncio.run(scenario())


def test_registered_extension_resource_updates_manifest_openapi_and_routes() -> None:
    async def scenario() -> None:
        first_id = new_id("widget")
        second_id = new_id("widget")
        service = InMemoryResourceService(
            (
                {"id": first_id, "type": "widget", "name": "Alpha", "role": "planner"},
                {"id": second_id, "type": "widget", "name": "Beta", "role": "executor"},
            )
        )
        control_plane, http = _stack(resource_services={"widgets": service})
        assert control_plane.registered_collections == ("widgets",)

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert manifest.body["resources"] == list(PLATFORM_COLLECTIONS) + [
            "widgets",
            "timeline",
        ]

        openapi_response = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert openapi_response.status == 200
        assert isinstance(openapi_response.body, dict)
        paths = openapi_response.body["paths"]
        assert isinstance(paths, dict)
        assert "/api/v1/widgets" in paths
        assert "/api/v1/widgets/{resource_id}" in paths

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/widgets",
                headers=_headers(),
                query={"filter[role]": "planner", "fields": "name"},
            )
        )
        assert listed.status == 200
        items = _assert_page(listed.body, total=1)
        assert items == [{"id": first_id, "type": "widget", "name": "Alpha"}]

        loaded = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/widgets/{second_id}", headers=_headers())
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["name"] == "Beta"

    asyncio.run(scenario())


def test_unregistered_future_domains_are_not_predeclared() -> None:
    async def scenario() -> None:
        _, http = _stack()
        missing_resource = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/agents", headers=_headers())
        )
        assert missing_resource.status == 404
        assert isinstance(missing_resource.body, dict)
        assert missing_resource.body["code"] == "not_found"

        missing_command = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/approval.approve",
                headers=_headers(key="approve-1"),
                body={"resource_ref": "approval-test"},
            )
        )
        assert missing_command.status == 404
        assert isinstance(missing_command.body, dict)
        assert missing_command.body["code"] == "not_found"
        assert missing_command.body["details"] == {"command": "approval.approve"}

    asyncio.run(scenario())


def test_registered_command_receives_actor_correlation_and_idempotency_context() -> None:
    async def scenario() -> None:
        calls: list[tuple[RequestContext, str, dict[str, JsonValue]]] = []

        async def refresh_widget(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            calls.append((context, resource_ref, payload))
            return {"id": resource_ref, "type": "widget", "refreshed": True}

        control_plane, http = _stack(command_handlers={"widget.refresh": refresh_widget})
        assert control_plane.registered_commands == ("widget.refresh",)

        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert isinstance(manifest.body, dict)
        assert manifest.body["commands"] == ["widget.refresh"]

        openapi_response = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert isinstance(openapi_response.body, dict)
        paths = openapi_response.body["paths"]
        assert isinstance(paths, dict)
        assert "/api/v1/commands/{command}" in paths

        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/widget.refresh",
                headers=_headers(key="refresh-42"),
                body={"resource_ref": "widget-test", "reason": "maintenance"},
            )
        )
        assert response.status == 200
        assert response.body == {"id": "widget-test", "type": "widget", "refreshed": True}
        assert len(calls) == 1
        context, resource_ref, payload = calls[0]
        assert context.idempotency_key == "refresh-42"
        assert context.correlation_id == "correlation-extension"
        assert context.actor.principal_ref == "user:test"
        assert resource_ref == "widget-test"
        assert payload == {"reason": "maintenance"}

    asyncio.run(scenario())


def test_extension_operations_are_authorized_and_private_fields_are_rejected() -> None:
    async def scenario() -> None:
        denied = FakeAuthorizationProvider(allowed=False)
        service = InMemoryResourceService(
            ({"id": new_id("widget"), "type": "widget", "name": "Denied"},)
        )
        _, denied_http = _stack(authorization=denied, resource_services={"widgets": service})
        forbidden = await denied_http.handle(
            HTTPRequest(method="GET", path="/api/v1/widgets", headers=_headers())
        )
        assert forbidden.status == 403
        assert isinstance(forbidden.body, dict)
        assert forbidden.body["code"] == "forbidden"
        assert denied.calls[0].context.correlation_id == "correlation-extension"

        leaking_service = InMemoryResourceService(
            (
                {
                    "id": new_id("widget"),
                    "type": "widget",
                    "adapter_metadata": {"vendor": "private"},
                },
            )
        )
        _, leaking_http = _stack(resource_services={"widgets": leaking_service})
        leaked = await leaking_http.handle(
            HTTPRequest(method="GET", path="/api/v1/widgets", headers=_headers())
        )
        assert leaked.status == 500
        assert isinstance(leaked.body, dict)
        assert leaked.body["code"] == "contract_violation"

    asyncio.run(scenario())


def test_extension_registration_rejects_existing_routes_and_invalid_names() -> None:
    control_plane, _ = _stack()
    service = InMemoryResourceService()

    for reserved in ("tasks", "models", "model-providers", "commands"):
        with pytest.raises(ValueError):
            control_plane.register_resource_service(reserved, service)
    with pytest.raises(ValueError):
        control_plane.register_resource_service("Bad Name", service)

    async def handler(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del context, payload
        return {"id": resource_ref}

    with pytest.raises(ValueError):
        control_plane.register_command("Bad Command", handler)
