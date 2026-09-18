"""Browser-first account bootstrap over the canonical Authentication and authorization stores."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.security.async_authorization_policy import (
    LocalAuthorizationPolicyStore,
)
from ai_multi_agent_platform.security.authentication import safe_actor
from ai_multi_agent_platform.security.first_user_bootstrap import (
    FirstUserBootstrapService,
    FirstUserBootstrapUnavailable,
)

from .async_authentication import AuthenticatedControlPlaneHTTP as _AuthenticatedControlPlaneHTTP
from .authentication import _header, _relative_path, _required_string
from .http import HTTPRequest, HTTPResponse
from .models import APIException, api_exception_from_contract

BOOTSTRAP_STATUS_PATH = "/auth/bootstrap-status"


class AuthenticatedControlPlaneHTTP(_AuthenticatedControlPlaneHTTP):
    """Add minimal public initialization state and transactional first-user browser bootstrap."""

    def __init__(
        self,
        control_plane: Any,
        authentication: Any,
        *,
        authorization: LocalAuthorizationPolicyStore | None = None,
        first_user_bootstrap: FirstUserBootstrapService | None = None,
        **kwargs: Any,
    ) -> None:
        if first_user_bootstrap is None and authorization is not None:
            first_user_bootstrap = FirstUserBootstrapService(authentication, authorization)
        if first_user_bootstrap is not None:
            kwargs.setdefault("runtime_authentication", first_user_bootstrap.runtime_authentication)
        super().__init__(control_plane, authentication, **kwargs)
        self._first_user_bootstrap = first_user_bootstrap

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        relative = _relative_path(request.path)
        if (
            self._first_user_bootstrap is not None
            and request.method == "GET"
            and relative == BOOTSTRAP_STATUS_PATH
        ):
            request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
            correlation_id = _header(request.headers, "x-correlation-id") or request_id
            try:
                status = await self._first_user_bootstrap.status()
                return self._response(
                    200,
                    {
                        "state": status.state.value,
                        "bootstrap_available": status.bootstrap_available,
                        "password_policy": {
                            "min_length": status.password_min_length,
                            "max_bytes": status.password_max_bytes,
                        },
                    },
                    request_id,
                    correlation_id,
                )
            except ContractError as exc:
                return self._error_response(
                    api_exception_from_contract(exc),
                    request_id,
                    correlation_id,
                )
            except APIException as exc:
                return self._error_response(exc, request_id, correlation_id)

        return await super().handle(request)

    async def _public_auth_route_async(
        self,
        request: HTTPRequest,
        relative: str,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if (
            self._first_user_bootstrap is not None
            and request.method == "POST"
            and relative == "/auth/bootstrap-admin"
        ):
            username = _required_string(request.body, "username")
            password = _required_string(request.body, "password")
            try:
                result = await self._first_user_bootstrap.bootstrap(
                    username,
                    password,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
            except FirstUserBootstrapUnavailable as exc:
                raise APIException(
                    status=409,
                    code="bootstrap_unavailable",
                    message=str(exc),
                ) from exc

            response = self._response(
                201,
                {
                    "actor": safe_actor(result.login.actor),
                    "csrf_token": result.login.session.csrf_token,
                    "expires_at": result.login.session.expires_at.isoformat(),
                    "authorization_granted": result.authorization_granted,
                },
                request_id,
                correlation_id,
            )
            response.headers["set-cookie"] = self._session_cookie(
                result.login.session.token,
                result.login.session.expires_at,
            )
            return response

        return await super()._public_auth_route_async(
            request,
            relative,
            request_id=request_id,
            correlation_id=correlation_id,
        )


__all__ = ["BOOTSTRAP_STATUS_PATH", "AuthenticatedControlPlaneHTTP"]
