from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher


class _ControlPlaneStub:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()


def test_malformed_authentication_request_returns_invalid_request() -> None:
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    http = AuthenticatedControlPlaneHTTP(
        _ControlPlaneStub(),
        authentication,
        secure_cookie=False,
    )

    response = asyncio.run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/login",
                body={"username": "missing-password"},
            )
        )
    )

    assert response.status == 400
    assert "invalid_request" in str(response.body)


def test_authentication_route_shape_precedes_authentication() -> None:
    authentication = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )
    http = AuthenticatedControlPlaneHTTP(
        _ControlPlaneStub(),
        authentication,
        secure_cookie=False,
    )
    headers = {
        "x-request-id": "request-1330",
        "x-correlation-id": "correlation-1330",
    }

    cases = (
        ("GET", "/api/v1/auth/login", 405, "method_not_allowed"),
        ("POST", "/api/v1/auth/me", 405, "method_not_allowed"),
        ("GET", "/api/v1/auth/sessions/session_fixture:revoke", 405, "method_not_allowed"),
        ("GET", "/api/v1/auth/not-a-public-route", 404, "not_found"),
    )
    for method, route, expected_status, expected_code in cases:
        response = asyncio.run(
            http.handle(HTTPRequest(method=method, path=route, headers=headers))
        )
        assert response.status == expected_status
        assert response.body["code"] == expected_code
        assert response.body["request_id"] == "request-1330"
        assert response.body["correlation_id"] == "correlation-1330"

    protected = asyncio.run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/auth/me", headers=headers))
    )
    assert protected.status == 401
    assert protected.body["code"] == "unauthorized"
