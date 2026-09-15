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
