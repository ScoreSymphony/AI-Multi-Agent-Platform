from __future__ import annotations

import asyncio
from typing import Any

from ai_multi_agent_platform.control_plane import (
    CURRENT_COLLECTIONS,
    PLATFORM_COLLECTIONS,
    REQUIRED_COMMANDS,
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
    health_providers: tuple[FakeOrchestrator, ...] = (),
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
        health_providers=health_providers,
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _headers(*, key: str | None = None) -> dict[str, str]:
    headers = {
        "X-Request-Id": "request-full-scope",
        "X-Correlation-Id": "correlation-full-scope",
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


def test_manifest_and_openapi_cover_every_issue_32_collection_and_command() -> None:
    async def scenario() -> None:
        _, http = _stack()
        manifest = await http.handle(HTTPRequest(method="GET", path="/api/v1"))
        assert manifest.status == 200
        assert isinstance(manifest.body, dict)
        assert manifest.body["resources"] == list(PLATFORM_COLLECTIONS) + ["timeline"]
        assert manifest.body["commands"] == list(REQUIRED_COMMANDS)
        assert CURRENT_COLLECTIONS == PLATFORM_COLLECTIONS

        specification = build_openapi()
        paths = specification["paths"]
        assert isinstance(paths, dict)
        for collection in PLATFORM_COLLECTIONS:
            assert f"/api/v1/{collection}" in paths
        for path in (
            "/api/v1/approvals/{resource_id}:approve",
            "/api/v1/approvals/{resource_id}:deny",
            "/api/v1/adapters/{resource_id}:enable",
            "/api/v1/adapters/{resource_id}:disable",
            "/api/v1/workers/{resource_id}:drain",
            "/api/v1/workers/{resource_id}:restore",
            "/api/v1/automations/{resource_id}:test",
            "/api/v1/evaluations/{resource_id}:start",
            "/api/v1/commands/{command}",
        ):
            assert path in paths

    asyncio.run(scenario())


def test_registered_extension_resources_support_list_read_filter_and_fields() -> None:
    async def scenario() -> None:
        first_id = new_id("agent")
        second_id = new_id("agent")
        service = InMemoryResourceService(
            (
                {"id": first_id, "type": "agent", "name": "Alpha", "role": "planner"},
                {"id": second_id, "type": "agent", "name": "Beta", "role": "executor"},
            )
        )
        _, http = _stack(resource_services={"agents": service})

        listed = await http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/agents",
                headers=_headers(),
                query={"filter[role]": "planner", "fields": "name"},
            )
        )
        assert listed.status == 200
        items = _assert_page(listed.body, total=1)
        assert items == [{"id": first_id, "type": "agent", "name": "Alpha"}]

        loaded = await http.handle(
            HTTPRequest(method="GET", path=f"/api/v1/agents/{second_id}", headers=_headers())
        )
        assert loaded.status == 200
        assert isinstance(loaded.body, dict)
        assert loaded.body["name"] == "Beta"

    asyncio.run(scenario())


def test_unconfigured_resource_and_command_map_to_canonical_unavailable() -> None:
    async def scenario() -> None:
        _, http = _stack()
        missing_resource = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/models", headers=_headers())
        )
        assert missing_resource.status == 503
        assert isinstance(missing_resource.body, dict)
        assert missing_resource.body["code"] == "unavailable"
        assert missing_resource.body["retryable"] is True
        assert missing_resource.body["request_id"] == "request-full-scope"
        assert missing_resource.body["correlation_id"] == "correlation-full-scope"

        missing_command = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/adapters/adapter-test:disable",
                headers=_headers(key="disable-1"),
            )
        )
        assert missing_command.status == 503
        assert isinstance(missing_command.body, dict)
        assert missing_command.body["code"] == "unavailable"
        assert missing_command.body["details"] == {"command": "adapter.disable"}

    asyncio.run(scenario())


def test_provider_and_capability_inventory_never_exposes_adapter_metadata() -> None:
    async def scenario() -> None:
        provider = FakeOrchestrator()
        _, http = _stack(health_providers=(provider,))

        providers = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/providers", headers=_headers())
        )
        assert providers.status == 200
        provider_items = _assert_page(providers.body, total=1)
        assert isinstance(provider_items[0], dict)
        assert provider_items[0]["id"] == provider.descriptor.provider_id
        assert "adapter_metadata" not in provider_items[0]

        capabilities = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/capabilities", headers=_headers())
        )
        assert capabilities.status == 200
        capability_items = _assert_page(capabilities.body, total=1)
        assert isinstance(capability_items[0], dict)
        assert capability_items[0]["provider_ref"] == provider.descriptor.provider_id
        assert "adapter_metadata" not in capability_items[0]

    asyncio.run(scenario())


def test_registered_command_receives_actor_correlation_and_idempotency_context() -> None:
    async def scenario() -> None:
        calls: list[tuple[RequestContext, str, dict[str, object]]] = []

        async def disable_adapter(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            calls.append((context, resource_ref, payload))
            return {"id": resource_ref, "type": "adapter", "enabled": False}

        _, http = _stack(command_handlers={"adapter.disable": disable_adapter})
        response = await http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/adapters/adapter-test:disable",
                headers=_headers(key="disable-42"),
                body={"reason": "maintenance"},
            )
        )
        assert response.status == 200
        assert response.body == {"id": "adapter-test", "type": "adapter", "enabled": False}
        assert len(calls) == 1
        context, resource_ref, payload = calls[0]
        assert context.idempotency_key == "disable-42"
        assert context.correlation_id == "correlation-full-scope"
        assert context.actor.principal_ref == "user:test"
        assert resource_ref == "adapter-test"
        assert payload == {"reason": "maintenance"}

    asyncio.run(scenario())


def test_extension_operations_are_authorized_and_private_fields_are_rejected() -> None:
    async def scenario() -> None:
        denied = FakeAuthorizationProvider(allowed=False)
        service = InMemoryResourceService(
            ({"id": new_id("agent"), "type": "agent", "name": "Denied"},)
        )
        _, denied_http = _stack(authorization=denied, resource_services={"agents": service})
        forbidden = await denied_http.handle(
            HTTPRequest(method="GET", path="/api/v1/agents", headers=_headers())
        )
        assert forbidden.status == 403
        assert isinstance(forbidden.body, dict)
        assert forbidden.body["code"] == "forbidden"
        assert denied.calls[0].context.correlation_id == "correlation-full-scope"

        leaking_service = InMemoryResourceService(
            (
                {
                    "id": new_id("agent"),
                    "type": "agent",
                    "adapter_metadata": {"vendor": "private"},
                },
            )
        )
        _, leaking_http = _stack(resource_services={"agents": leaking_service})
        leaked = await leaking_http.handle(
            HTTPRequest(method="GET", path="/api/v1/agents", headers=_headers())
        )
        assert leaked.status == 500
        assert isinstance(leaked.body, dict)
        assert leaked.body["code"] == "contract_violation"

    asyncio.run(scenario())
