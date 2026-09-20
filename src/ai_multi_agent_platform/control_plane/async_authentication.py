"""Async-safe authenticated Control Plane boundary for blocking local persistence."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.async_authentication import (
    AsyncAuthenticationService,
    runtime_authentication_service,
)
from ai_multi_agent_platform.security.authentication import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    safe_actor,
    safe_mobile_device,
    safe_session,
)
from ai_multi_agent_platform.security.authentication_hardening import (
    CredentialScope,
    LocalAuthenticationService,
    safe_credential_with_scope,
)

from .authentication import (
    _augment_authentication_openapi,
    _cookies,
    _header,
    _optional_datetime,
    _optional_string,
    _protocol_version,
    _public_route,
    _require_only_fields,
    _relative_path,
    _required_string,
)
from .http import HTTPRequest, HTTPResponse
from .models import APIException, api_exception_from_contract
from .release_api import AuthenticatedControlPlaneHTTP as _ReleaseAuthenticatedControlPlaneHTTP


class AuthenticatedControlPlaneHTTP(_ReleaseAuthenticatedControlPlaneHTTP):
    """Current authenticated HTTP surface with awaitable Authentication persistence.

    The inherited synchronous Authentication methods remain compatibility seams for explicit
    setup/offline callers. Normal HTTP and async stream preparation use the backend-neutral
    ``AsyncAuthenticationService`` so SQLite write-through metadata never runs inline on the
    event-loop thread.
    """

    def __init__(
        self,
        control_plane: Any,
        authentication: LocalAuthenticationService,
        *,
        runtime_authentication: AsyncAuthenticationService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(control_plane, authentication, **kwargs)
        self._runtime_authentication = runtime_authentication_service(
            authentication,
            runtime_service=runtime_authentication,
        )

    @property
    def runtime_authentication(self) -> AsyncAuthenticationService:
        return self._runtime_authentication

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            relative = _relative_path(request.path)

            if relative.startswith("/auth/"):
                if _public_route(request.method, relative):
                    response = await self._public_auth_route_async(
                        request,
                        relative,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                else:
                    actor, session_token = await self._authenticate_request_async(
                        request,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                    response = await self._authenticated_auth_route_async(
                        request,
                        relative,
                        actor,
                        session_token=session_token,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                return self._augment_openapi_if_needed(request, response)

            if _public_route(request.method, relative):
                response = await self._current_http.handle(request)
                if (
                    request.method == "GET"
                    and relative == "/openapi.json"
                    and response.status == 200
                    and isinstance(response.body, dict)
                ):
                    response = HTTPResponse(
                        status=response.status,
                        body=cast(
                            dict[str, JsonValue],
                            _augment_authentication_openapi(
                                cast(dict[str, Any], response.body),
                                cookie_name=self._cookie_name,
                            ),
                        ),
                        headers=response.headers,
                    )
                return self._augment_openapi_if_needed(request, response)

            actor, _ = await self._authenticate_request_async(
                request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            trusted = self._trusted_request(
                request,
                actor,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            response = await self._current_http.handle(trusted)
            return self._augment_openapi_if_needed(request, response)
        except ContractError as exc:
            return self._error_response(
                api_exception_from_contract(exc),
                request_id,
                correlation_id,
            )
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)
        except KeyError:
            return self._error(
                status=404,
                code="not_found",
                message="authentication resource not found",
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except (TypeError, ValueError) as exc:
            return self._error(
                status=400,
                code="invalid_request",
                message=str(exc),
                request_id=request_id,
                correlation_id=correlation_id,
            )

    async def async_prepare_stream_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest | HTTPResponse:
        """Authenticate an async stream without blocking its event-loop transport."""

        try:
            actor, _ = await self._authenticate_request_async(
                request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return self._trusted_request(
                request,
                actor,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except ContractError as exc:
            return self._error_response(
                api_exception_from_contract(exc),
                request_id,
                correlation_id,
            )
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)
        except (KeyError, TypeError, ValueError):
            return self._authentication_error(
                AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS),
                request_id,
                correlation_id,
            )

    async def _authenticate_request_async(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> tuple[AuthenticatedActor, str | None]:
        authorization = _header(request.headers, "authorization")
        if authorization is not None:
            scheme, separator, value = authorization.partition(" ")
            if not separator or scheme.casefold() != "bearer" or not value.strip():
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            actor = await self._runtime_authentication.authenticate_bearer(
                value.strip(),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            await self._runtime_authentication.check_authenticated_request(actor)
            return actor, None

        cookies = _cookies(_header(request.headers, "cookie") or "")
        session_token = cookies.get(self._cookie_name)
        if session_token is None:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        require_csrf = request.method not in {"GET", "HEAD", "OPTIONS"}
        actor = await self._runtime_authentication.authenticate_session(
            session_token,
            csrf_token=_header(request.headers, "x-csrf-token"),
            require_csrf=require_csrf,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        await self._runtime_authentication.check_authenticated_request(actor)
        return actor, session_token

    async def _public_auth_route_async(
        self,
        request: HTTPRequest,
        relative: str,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if request.method == "POST" and relative == "/auth/bootstrap-admin":
            username = _required_string(request.body, "username")
            password = _required_string(request.body, "password")
            account = await self._runtime_authentication.bootstrap_first_admin(
                username,
                password,
                correlation_id=correlation_id,
            )
            return self._response(
                201,
                {
                    "id": account.user_id,
                    "username": account.username,
                    "enabled": account.enabled,
                    "locked": account.locked,
                    "created_at": account.created_at.isoformat(),
                    "authorization_granted": False,
                },
                request_id,
                correlation_id,
            )

        if request.method == "POST" and relative == "/auth/login":
            username = _required_string(request.body, "username")
            password = _required_string(request.body, "password")
            result = await self._runtime_authentication.login(
                username,
                password,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            response = self._response(
                200,
                {
                    "actor": safe_actor(result.actor),
                    "csrf_token": result.session.csrf_token,
                    "expires_at": result.session.expires_at.isoformat(),
                },
                request_id,
                correlation_id,
            )
            response.headers["set-cookie"] = self._session_cookie(
                result.session.token,
                result.session.expires_at,
            )
            return response

        if request.method == "POST" and relative == "/auth/mobile-pairings:consume":
            _require_only_fields(
                request.body,
                {
                    "pairing_code",
                    "pairing_id",
                    "server_origin",
                    "device_name",
                    "device_platform",
                    "protocol_version",
                },
            )
            grant = await self._runtime_authentication.consume_mobile_pairing(
                _required_string(request.body, "pairing_code"),
                server_origin=_required_string(request.body, "server_origin"),
                device_name=_required_string(request.body, "device_name"),
                device_platform=_required_string(request.body, "device_platform"),
                pairing_id=_optional_string(request.body.get("pairing_id")),
                protocol_version=_protocol_version(request.body.get("protocol_version")),
                correlation_id=correlation_id,
            )
            return self._response(
                201,
                {
                    "device": safe_mobile_device(
                        grant.device,
                        self._hardened_authentication.store.credentials.get(
                            grant.device.credential_id
                        ),
                    ),
                    "credential": {
                        "id": grant.credential.credential_id,
                        "secret": grant.credential.secret,
                        "expires_at": (
                            grant.credential.expires_at.isoformat()
                            if grant.credential.expires_at
                            else None
                        ),
                        "secret_display": "one_time",
                    },
                },
                request_id,
                correlation_id,
            )

        return self._error(
            status=404,
            code="not_found",
            message="authentication route not found",
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def _authenticated_auth_route_async(
        self,
        request: HTTPRequest,
        relative: str,
        actor: AuthenticatedActor,
        *,
        session_token: str | None,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if request.method == "GET" and relative == "/auth/me":
            return self._response(200, safe_actor(actor), request_id, correlation_id)

        if request.method == "POST" and relative == "/auth/logout":
            if session_token is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            await self._runtime_authentication.logout(session_token)
            response = self._response(
                200,
                {"logged_out": True},
                request_id,
                correlation_id,
            )
            response.headers["set-cookie"] = self._clear_session_cookie()
            return response

        if request.method == "POST" and relative == "/auth/session:renew":
            if session_token is None or actor.method is not AuthenticationMethod.BROWSER_SESSION:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            if actor.credential_id is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            grant = await self._runtime_authentication.renew_browser_session(
                actor.identity.actor_id,
                actor.credential_id,
            )
            response = self._response(
                200,
                {
                    "csrf_token": grant.csrf_token,
                    "expires_at": grant.expires_at.isoformat(),
                },
                request_id,
                correlation_id,
            )
            response.headers["set-cookie"] = self._session_cookie(grant.token, grant.expires_at)
            return response

        if actor.identity.actor_type.value != "human":
            return self._error(
                status=403,
                code="forbidden",
                message="human authentication is required for this account operation",
                request_id=request_id,
                correlation_id=correlation_id,
            )
        user_id = actor.identity.actor_id

        if request.method == "GET" and relative == "/auth/sessions":
            sessions = await self._runtime_authentication.list_sessions(user_id)
            items: list[JsonValue] = [safe_session(item) for item in sessions]
            return self._response(200, {"items": items}, request_id, correlation_id)

        if request.method == "POST" and relative.startswith("/auth/sessions/"):
            suffix = relative.removeprefix("/auth/sessions/")
            if suffix.endswith(":revoke"):
                session_id = suffix.removesuffix(":revoke")
                await self._runtime_authentication.revoke_session(user_id, session_id)
                return self._response(
                    200,
                    {"id": session_id, "revoked": True},
                    request_id,
                    correlation_id,
                )

        if request.method == "POST" and relative == "/auth/password:change":
            current_password = _required_string(request.body, "current_password")
            new_password = _required_string(request.body, "new_password")
            await self._runtime_authentication.change_password(
                user_id,
                current_password,
                new_password,
            )
            response = self._response(
                200,
                {"password_changed": True, "sessions_invalidated": True},
                request_id,
                correlation_id,
            )
            if session_token is not None:
                response.headers["set-cookie"] = self._clear_session_cookie()
            return response

        if request.method == "POST" and relative == "/auth/mobile-pairings":
            _require_only_fields(request.body, {"server_origin"})
            await self._authorize_credential_operation(
                request,
                actor,
                action="create",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
                bind_payload=True,
            )
            pairing = await self._runtime_authentication.create_mobile_pairing(
                user_id,
                _required_string(request.body, "server_origin"),
                correlation_id=correlation_id,
            )
            return self._response(
                201,
                {
                    "id": pairing.pairing_id,
                    "pairing_code": pairing.pairing_code,
                    "server_origin": pairing.server_origin,
                    "protocol_version": pairing.protocol_version,
                    "created_at": pairing.created_at.isoformat(),
                    "expires_at": pairing.expires_at.isoformat(),
                    "qr_payload": pairing.qr_payload,
                    "secret_display": "one_time",
                },
                request_id,
                correlation_id,
            )

        if request.method == "POST" and relative.startswith("/auth/mobile-pairings/"):
            suffix = relative.removeprefix("/auth/mobile-pairings/")
            if suffix.endswith(":cancel"):
                pairing_id = suffix.removesuffix(":cancel")
                await self._authorize_credential_operation(
                    request,
                    actor,
                    action="revoke",
                    resource_ref=pairing_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                await self._runtime_authentication.cancel_mobile_pairing(
                    user_id,
                    pairing_id,
                    correlation_id=correlation_id,
                )
                return self._response(
                    200,
                    {"id": pairing_id, "cancelled": True},
                    request_id,
                    correlation_id,
                )

        if request.method == "GET" and relative == "/auth/mobile-devices":
            await self._authorize_credential_operation(
                request,
                actor,
                action="list",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            mobile_devices = await self._runtime_authentication.list_mobile_devices(user_id)
            items: list[JsonValue] = [
                safe_mobile_device(
                    item,
                    self._hardened_authentication.store.credentials.get(item.credential_id),
                )
                for item in mobile_devices
            ]
            return self._response(200, {"items": items}, request_id, correlation_id)

        if request.method == "POST" and relative == "/auth/mobile-devices:revoke-all":
            await self._authorize_credential_operation(
                request,
                actor,
                action="revoke",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            revoked = await self._runtime_authentication.revoke_all_mobile_devices(
                user_id,
                correlation_id=correlation_id,
            )
            return self._response(200, {"revoked": revoked}, request_id, correlation_id)

        if request.method == "POST" and relative.startswith("/auth/mobile-devices/"):
            suffix = relative.removeprefix("/auth/mobile-devices/")
            if suffix.endswith(":revoke"):
                device_id = suffix.removesuffix(":revoke")
                await self._authorize_credential_operation(
                    request,
                    actor,
                    action="revoke",
                    resource_ref=device_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                await self._runtime_authentication.revoke_mobile_device(
                    user_id,
                    device_id,
                    correlation_id=correlation_id,
                )
                return self._response(
                    200,
                    {"id": device_id, "revoked": True},
                    request_id,
                    correlation_id,
                )

        if request.method == "GET" and relative == "/auth/credentials":
            await self._authorize_credential_operation(
                request,
                actor,
                action="list",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            credentials = await self._runtime_authentication.list_credentials(user_id)
            items = [
                safe_credential_with_scope(self._hardened_authentication, item)
                for item in credentials
            ]
            return self._response(200, {"items": items}, request_id, correlation_id)

        if request.method == "POST" and relative == "/auth/credentials":
            await self._authorize_credential_operation(
                request,
                actor,
                action="create",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
                bind_payload=True,
            )
            purpose = _required_string(request.body, "purpose")
            expires_at = _optional_datetime(request.body.get("expires_at"))
            scope = CredentialScope.from_json(request.body.get("scope"))
            issued = await self._runtime_authentication.create_personal_access_token(
                user_id,
                purpose=purpose,
                expires_at=expires_at,
                scope=scope,
            )
            return self._response(
                201,
                {
                    "id": issued.credential_id,
                    "secret": issued.secret,
                    "expires_at": issued.expires_at.isoformat() if issued.expires_at else None,
                    "scope": scope.to_json(),
                    "secret_display": "one_time",
                },
                request_id,
                correlation_id,
            )

        if request.method == "POST" and relative.startswith("/auth/credentials/"):
            suffix = relative.removeprefix("/auth/credentials/")
            if suffix.endswith(":revoke"):
                credential_id = suffix.removesuffix(":revoke")
                await self._authorize_credential_operation(
                    request,
                    actor,
                    action="revoke",
                    resource_ref=credential_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                await self._runtime_authentication.revoke_credential(user_id, credential_id)
                return self._response(
                    200,
                    {"id": credential_id, "revoked": True},
                    request_id,
                    correlation_id,
                )

        return self._error(
            status=404,
            code="not_found",
            message="authentication route not found",
            request_id=request_id,
            correlation_id=correlation_id,
        )


__all__ = ["AuthenticatedControlPlaneHTTP"]
