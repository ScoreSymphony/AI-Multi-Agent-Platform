"""Browser-first bootstrap boundary for a fresh platform installation.

The ordinary authenticated Control Plane remains authoritative after the first local
administrator exists. This boundary exposes only the minimal public state required to
choose between first-user creation and normal sign-in, and it composes the existing
authentication and authorization services rather than creating a second auth system.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import ActorType, LocalPrincipalPolicy
from ai_multi_agent_platform.security.authentication import (
    AuthenticationError,
    LocalAuthenticationService,
    safe_actor,
)

from .async_authentication import AuthenticatedControlPlaneHTTP
from .http import HTTPRequest, HTTPResponse

_PASSWORD_MINIMUM_LENGTH = 12
_PASSWORD_MAXIMUM_BYTES = 1024


class AdministratorPolicyStore(Protocol):
    """Minimal #15 policy surface required by first-user bootstrap."""

    def has_policy(self, principal_ref: str) -> bool: ...

    def register(self, policy: LocalPrincipalPolicy) -> None: ...


class BrowserFirstRunControlPlaneHTTP(AuthenticatedControlPlaneHTTP):
    """Add one-time browser bootstrap before delegating to authenticated HTTP.

    Discovery of the first-run state is deliberately tiny and unauthenticated. Account
    creation is serialized within the serving process, reuses the canonical #36 local
    authentication service, installs an explicit #15 administrator policy, and creates the
    same HttpOnly browser session used by ordinary login.
    """

    def __init__(
        self,
        control_plane: Any,
        authentication: LocalAuthenticationService,
        authorization: AdministratorPolicyStore,
        *,
        cookie_name: str = "amp_session",
        secure_cookie: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            control_plane,
            authentication,
            cookie_name=cookie_name,
            secure_cookie=secure_cookie,
            **kwargs,
        )
        self._bootstrap_authorization = authorization
        self._bootstrap_lock = asyncio.Lock()

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        relative = _relative_path(request.path)
        if request.method == "GET" and relative == "/auth/bootstrap-status":
            return self._bootstrap_status(request)
        if request.method == "POST" and relative == "/auth/bootstrap-admin":
            return await self._bootstrap_admin(request)
        return await super().handle(request)

    def _bootstrap_status(self, request: HTTPRequest) -> HTTPResponse:
        request_id, correlation_id = _request_ids(request)
        initialized = bool(self._authentication.store.users)
        return self._response(
            200,
            {
                "state": "initialized" if initialized else "uninitialized",
                "bootstrap_available": not initialized,
                "password_policy": {
                    "minimum_length": _PASSWORD_MINIMUM_LENGTH,
                    "maximum_bytes": _PASSWORD_MAXIMUM_BYTES,
                },
            },
            request_id,
            correlation_id,
        )

    async def _bootstrap_admin(self, request: HTTPRequest) -> HTTPResponse:
        request_id, correlation_id = _request_ids(request)
        try:
            username = _required_string(request.body, "username")
            password = _required_string(request.body, "password")
            password_confirmation = _required_string(request.body, "password_confirmation")
            if password != password_confirmation:
                raise ValueError("password confirmation does not match")

            async with self._bootstrap_lock:
                if self._authentication.store.users:
                    return self._error(
                        status=409,
                        code="bootstrap_unavailable",
                        message="first-user bootstrap is no longer available",
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )

                account = await self.runtime_authentication.bootstrap_first_admin(
                    username,
                    password,
                    correlation_id=correlation_id,
                )
                await asyncio.to_thread(self._install_administrator_policy, account.user_id)
                result = await self.runtime_authentication.login(
                    username,
                    password,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )

            response = self._response(
                201,
                {
                    "actor": safe_actor(result.actor),
                    "csrf_token": result.session.csrf_token,
                    "expires_at": result.session.expires_at.isoformat(),
                    "authorization_granted": True,
                },
                request_id,
                correlation_id,
            )
            response.headers["set-cookie"] = self._session_cookie(
                result.session.token,
                result.session.expires_at,
            )
            return response
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
        except (TypeError, ValueError) as exc:
            return self._error(
                status=400,
                code="invalid_request",
                message=str(exc),
                request_id=request_id,
                correlation_id=correlation_id,
            )

    def _install_administrator_policy(self, principal_ref: str) -> None:
        if self._bootstrap_authorization.has_policy(principal_ref):
            return
        self._bootstrap_authorization.register(
            LocalPrincipalPolicy(
                principal_ref=principal_ref,
                actor_types=frozenset({ActorType.HUMAN}),
                administrator=True,
            )
        )


def _request_ids(request: HTTPRequest) -> tuple[str, str]:
    request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
    correlation_id = _header(request.headers, "x-correlation-id") or request_id
    return request_id, correlation_id


def _header(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.casefold()
    for key, value in headers.items():
        if key.casefold() == expected:
            return value
    return None


def _required_string(body: Mapping[str, JsonValue], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _relative_path(path: str) -> str:
    normalized = "/" + path.strip("/")
    prefix = "/api/v1"
    if normalized == prefix:
        return ""
    if normalized.startswith(prefix + "/"):
        return normalized[len(prefix) :]
    return normalized


__all__ = ["BrowserFirstRunControlPlaneHTTP"]
