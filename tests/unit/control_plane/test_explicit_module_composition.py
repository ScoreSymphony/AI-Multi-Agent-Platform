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

    assert (
        first.registered_modules
        == second.registered_modules
        == (
            "domain.alpha",
            "domain.beta",
        )
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


def test_failed_module_batch_is_atomic() -> None:
    control_plane = _control_plane()
    alpha = ControlPlaneModule(
        name="domain.alpha",
        resource_services={"widgets": InMemoryResourceService()},
        command_handlers={"widget.refresh": _command},
    )
    beta = ControlPlaneModule(
        name="domain.beta",
        resource_services={"widgets": InMemoryResourceService()},
    )

    with pytest.raises(ValueError, match="duplicate Control Plane resource ownership"):
        control_plane.register_modules((alpha, beta))

    assert control_plane.registered_modules == ()
    assert control_plane.registered_collections == ()
    assert control_plane.registered_commands == ()
    assert control_plane.resource_owner("widgets") is None
    assert control_plane.command_owner("widget.refresh") is None


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


def test_command_authorizer_must_belong_to_same_module_command() -> None:
    async def authorize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        del context, resource_ref, payload

    with pytest.raises(ValueError, match="must belong to commands owned by the same module"):
        ControlPlaneModule(
            name="domain.alpha",
            command_authorizers={"widget.refresh": authorize},
        )


def test_explicit_command_authorizer_and_observer_run_in_declared_boundary() -> None:
    calls: list[tuple[str, str]] = []

    async def authorize(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> None:
        del context, payload
        calls.append(("authorize", resource_ref))

    async def observe(
        context: RequestContext,
        command: str,
        resource_ref: str,
        result: dict[str, JsonValue],
    ) -> None:
        del context
        assert result == {"id": resource_ref, "type": "widget"}
        calls.append(("observe", command))

    module = ControlPlaneModule(
        name="domain.alpha",
        command_handlers={"widget.refresh": _command},
        command_authorizers={"widget.refresh": authorize},
        command_observers=(observe,),
    )
    control_plane = _control_plane(module)
    context = RequestContext(
        request_id="request-policy",
        correlation_id="correlation-policy",
        idempotency_key="idempotency-policy",
    )

    result = asyncio.run(
        control_plane.execute_command(
            context,
            "widget.refresh",
            "widget-1",
            {"reason": "test"},
        )
    )

    assert result == {"id": "widget-1", "type": "widget"}
    assert calls == [
        ("authorize", "widget-1"),
        ("observe", "widget.refresh"),
    ]


def test_command_observers_run_in_deterministic_module_order() -> None:
    calls: list[str] = []

    def observer(name: str):
        async def observe(
            context: RequestContext,
            command: str,
            resource_ref: str,
            result: dict[str, JsonValue],
        ) -> None:
            del context, command, resource_ref, result
            calls.append(name)

        return observe

    alpha = ControlPlaneModule(
        name="domain.alpha",
        command_observers=(observer("alpha"),),
    )
    beta = ControlPlaneModule(
        name="domain.beta",
        command_handlers={"widget.refresh": _command},
        command_observers=(observer("beta"),),
    )
    control_plane = _control_plane(beta, alpha)
    context = RequestContext(
        request_id="request-observers",
        correlation_id="correlation-observers",
        idempotency_key="idempotency-observers",
    )

    asyncio.run(control_plane.execute_command(context, "widget.refresh", "widget-1", {}))

    assert calls == ["alpha", "beta"]


def test_normalized_special_route_ownership_conflict_is_rejected() -> None:
    async def status(request: HTTPRequest) -> HTTPResponse:
        del request
        return HTTPResponse(status=200, body={"status": "ok"})

    alpha = ControlPlaneModule(
        name="domain.alpha",
        routes=(ControlPlaneRoute("get", "/api/v1/module-status/", status),),
    )
    beta = ControlPlaneModule(
        name="domain.beta",
        routes=(ControlPlaneRoute("GET", "/api/v1/module-status", status),),
    )

    with pytest.raises(ValueError, match="duplicate Control Plane route ownership"):
        _control_plane(alpha, beta)


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
        assert set(paths["/api/v1/module-status"]) == {"get"}

        wrong_method = await http.handle(HTTPRequest(method="POST", path="/api/v1/module-status"))
        assert wrong_method.status == 405
        assert isinstance(wrong_method.body, dict)
        assert wrong_method.body["code"] == "method_not_allowed"
        assert wrong_method.body["category"] == "transport"

        unknown_nested = await http.handle(
            HTTPRequest(method="GET", path="/api/v1/module-status/does-not-exist")
        )
        assert unknown_nested.status == 404
        assert isinstance(unknown_nested.body, dict)
        assert unknown_nested.body["code"] == "not_found"
        assert unknown_nested.body["category"] == "resource"

    asyncio.run(scenario())


def test_openapi_contributors_run_in_deterministic_module_order() -> None:
    def contribute(name: str):
        def apply(specification: dict[str, Any]) -> None:
            order = specification.setdefault("x-module-order", [])
            assert isinstance(order, list)
            order.append(name)

        return apply

    alpha = ControlPlaneModule(
        name="domain.alpha",
        openapi_contributors=(contribute("alpha"),),
    )
    beta = ControlPlaneModule(
        name="domain.beta",
        openapi_contributors=(contribute("beta"),),
    )

    first = _control_plane(beta, alpha)
    second = _control_plane(alpha, beta)
    first_spec = first.apply_openapi_contributions({})
    second_spec = second.apply_openapi_contributions({})

    assert first_spec["x-module-order"] == ["alpha", "beta"]
    assert second_spec["x-module-order"] == ["alpha", "beta"]
