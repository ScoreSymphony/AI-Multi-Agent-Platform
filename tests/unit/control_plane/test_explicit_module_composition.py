from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.extensions import (
    ControlPlane,
    ControlPlaneHTTP,
    ControlPlaneModule,
    ControlPlaneRoute,
    InMemoryResourceService,
)
from ai_multi_agent_platform.control_plane.http import HTTPRequest, HTTPResponse
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _control_plane(*modules: ControlPlaneModule) -> ControlPlane:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    return ControlPlane(kernel=kernel, events=repository, modules=modules)


async def _command(
    context: RequestContext,
    resource_ref: str,
    payload: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    del context, payload
    return {"id": resource_ref, "type": "widget"}


def test_module_batch_is_order_independent_and_ownership_is_inspectable() -> None:
    alpha = ControlPlaneModule(
        name="domain.alpha",
        resource_services={"widgets": InMemoryResourceService()},
    )
    beta = ControlPlaneModule(
        name="domain.beta",
        command_handlers={"widget.refresh": _command},
        requires=frozenset({"domain.alpha"}),
    )

    first = _control_plane(alpha, beta)
    second = _control_plane(beta, alpha)

    assert first.registered_modules == second.registered_modules == (
        "domain.alpha",
        "domain.beta",
    )
    assert first.registered_collections == second.registered_collections == ("widgets",)
    assert first.registered_commands == second.registered_commands == ("widget.refresh",)
    assert first.resource_owner("widgets") == "domain.alpha"
    assert first.command_owner("widget.refresh") == "domain.beta"


def test_duplicate_module_ownership_is_rejected_before_installation() -> None:
    first = ControlPlaneModule(
        name="domain.alpha",
        resource_services={"widgets": InMemoryResourceService()},
    )
    second = ControlPlaneModule(
        name="domain.beta",
        resource_services={"widgets": InMemoryResourceService()},
    )

    with pytest.raises(ValueError, match="duplicate Control Plane resource ownership"):
        _control_plane(second, first)


def test_duplicate_manual_registration_does_not_silently_replace_owner() -> None:
    control_plane = _control_plane()
    control_plane.register_command("widget.refresh", _command, owner="domain.alpha")

    with pytest.raises(ValueError, match="domain.alpha.*domain.beta"):
        control_plane.register_command("widget.refresh", _command, owner="domain.beta")

    assert control_plane.command_owner("widget.refresh") == "domain.alpha"


def test_missing_module_dependency_is_rejected() -> None:
    module = ControlPlaneModule(
        name="domain.beta",
        requires=frozenset({"domain.alpha"}),
    )

    with pytest.raises(ValueError, match="requires missing modules"):
        _control_plane(module)


def test_special_route_and_openapi_contribution_have_explicit_owner() -> None:
    async def status(request: HTTPRequest) -> HTTPResponse:
        del request
        return HTTPResponse(status=200, body={"status": "ok"})

    def contribute(specification: dict[str, Any]) -> None:
        paths = specification["paths"]
        assert isinstance(paths, dict)
        paths["/api/v1/module-status"] = {
            "get": {
                "operationId": "getModuleStatus",
                "responses": {"200": {"description": "module status"}},
            }
        }

    module = ControlPlaneModule(
        name="domain.status",
        routes=(ControlPlaneRoute("GET", "/api/v1/module-status", status),),
        openapi_contributors=(contribute,),
    )
    control_plane = _control_plane(module)
    http = ControlPlaneHTTP(control_plane)

    assert control_plane.route_owner("GET", "/api/v1/module-status/") == "domain.status"

    async def scenario() -> None:
        response = await http.handle(HTTPRequest(method="GET", path="/api/v1/module-status"))
        assert response.status == 200
        assert response.body == {"status": "ok"}
        assert "X-Request-Id" in response.headers
        assert "X-Correlation-Id" in response.headers

        openapi = await http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json"))
        assert openapi.status == 200
        assert isinstance(openapi.body, dict)
        paths = openapi.body["paths"]
        assert isinstance(paths, dict)
        assert "/api/v1/module-status" in paths

    asyncio.run(scenario())
