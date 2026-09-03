"""Authenticated Control Plane transport boundary for issue #36."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.authentication import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    LocalAuthenticationService,
    safe_actor,
    safe_credential,
    safe_session,
)

from .http import HTTPRequest, HTTPResponse, _request_context
from .models import API_VERSION, APIError, APIException, api_exception_from_contract
from .workspace_task_management_api import ControlPlaneHTTP as _ControlPlaneHTTP


class AuthenticatedControlPlaneHTTP(_ControlPlaneHTTP):
    """Authenticate northbound requests before projecting identity into #15 context.

    Caller-supplied principal/owner headers are never trusted. After authentication the
    boundary injects only the canonical actor identity established by the authentication
    service. Authorization decisions remain delegated to the composed Control Plane.
    """

    def __init__(
        self,
        control_plane: Any,
        authentication: LocalAuthenticationService,
        *,
        cookie_name: str = "amp_session",
        secure_cookie: bool = True,
    ) -> None:
        super().__init__(control_plane)
        if not cookie_name.strip():
            raise ValueError("cookie_name must not be blank")
        self._authentication = authentication
        self._cookie_name = cookie_name
        self._secure_cookie = secure_cookie

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            relative = _relative_path(request.path)
            if _public_route(request.method, relative):
                if relative.startswith("/auth/"):
                    return self._public_auth_route(
                        request,
                        relative,
                        request_id=request_id,
                        correlation_id=correlation_id,
                    )
                response = await super().handle(
                    _with_request_ids(request, request_id, correlation_id)
                )
                if request.method == "GET" and relative == "/openapi.json":
                    if not isinstance(response.body, dict):
                        raise APIException(
                            status=500,
                            code="contract_violation",
                            message="OpenAPI response must be an object",
                        )
                    return HTTPResponse(
                        status=response.status,
                        body=_augment_authentication_openapi(
                            response.body,
                            cookie_name=self._cookie_name,
                        ),
                        headers=response.headers,
                    )
                return response

            actor, session_token = self._authenticate_request(
                request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            if relative.startswith("/auth/"):
                return await self._authenticated_auth_route(
                    request,
                    relative,
                    actor,
                    session_token=session_token,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )

            trusted = _with_authenticated_actor(
                request,
                actor,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return await super().handle(trusted)
        except ContractError as exc:
            return self._error_response(
                api_exception_from_contract(exc),
                request_id,
                correlation_id,
            )
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
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

    def prepare_stream_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest | HTTPResponse:
        """Authenticate SSE before the transport constructs its RequestContext."""

        try:
            actor, _ = self._authenticate_request(
                request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
        return _with_authenticated_actor(
            request,
            actor,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def _public_auth_route(
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
            account = self._authentication.bootstrap_first_admin(
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
            result = self._authentication.login(
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

        return self._error(
            status=404,
            code="not_found",
            message="authentication route not found",
            request_id=request_id,
            correlation_id=correlation_id,
        )

    async def _authenticated_auth_route(
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
            self._authentication.logout(session_token)
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
            self._authentication.revoke_session(
                actor.identity.actor_id,
                actor.credential_id,
            )
            grant = self._authentication.create_browser_session(actor.identity.actor_id)
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
            sessions: list[JsonValue] = [
                safe_session(item) for item in self._authentication.list_sessions(user_id)
            ]
            return self._response(200, {"items": sessions}, request_id, correlation_id)

        if request.method == "POST" and relative.startswith("/auth/sessions/"):
            suffix = relative.removeprefix("/auth/sessions/")
            if suffix.endswith(":revoke"):
                session_id = suffix.removesuffix(":revoke")
                self._authentication.revoke_session(user_id, session_id)
                return self._response(
                    200,
                    {"id": session_id, "revoked": True},
                    request_id,
                    correlation_id,
                )

        if request.method == "POST" and relative == "/auth/password:change":
            current_password = _required_string(request.body, "current_password")
            new_password = _required_string(request.body, "new_password")
            self._authentication.change_password(user_id, current_password, new_password)
            response = self._response(
                200,
                {"password_changed": True, "sessions_invalidated": True},
                request_id,
                correlation_id,
            )
            if session_token is not None:
                response.headers["set-cookie"] = self._clear_session_cookie()
            return response

        if request.method == "GET" and relative == "/auth/credentials":
            await self._authorize_credential_operation(
                request,
                actor,
                action="list",
                resource_ref=user_id,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            credentials: list[JsonValue] = [
                safe_credential(item) for item in self._authentication.list_credentials(user_id)
            ]
            return self._response(200, {"items": credentials}, request_id, correlation_id)

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
            issued = self._authentication.create_personal_access_token(
                user_id,
                purpose=purpose,
                expires_at=expires_at,
            )
            return self._response(
                201,
                {
                    "id": issued.credential_id,
                    "secret": issued.secret,
                    "expires_at": issued.expires_at.isoformat() if issued.expires_at else None,
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
                self._authentication.revoke_credential(user_id, credential_id)
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

    async def _authorize_credential_operation(
        self,
        request: HTTPRequest,
        actor: AuthenticatedActor,
        *,
        action: str,
        resource_ref: str,
        request_id: str,
        correlation_id: str,
        bind_payload: bool = False,
    ) -> None:
        """Apply the canonical #15 gate before credential-management side effects."""

        authorize = getattr(self._control_plane, "_authorize", None)
        if authorize is None:
            raise APIException(
                status=503,
                code="authorization_unavailable",
                message="credential management requires the canonical authorization boundary",
            )
        trusted = _with_authenticated_actor(
            request,
            actor,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        context = _request_context(trusted, request_id, correlation_id)
        digest = _payload_digest(request.body) if bind_payload else None
        await authorize(
            context,
            f"credential:{action}",
            resource_ref,
            request_payload_digest=digest,
        )

    def _authenticate_request(
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
            actor = self._authentication.authenticate_bearer(
                value.strip(),
                request_id=request_id,
                correlation_id=correlation_id,
            )
            return actor, None

        cookies = _cookies(_header(request.headers, "cookie") or "")
        session_token = cookies.get(self._cookie_name)
        if session_token is None:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        require_csrf = request.method not in {"GET", "HEAD", "OPTIONS"}
        actor = self._authentication.authenticate_session(
            session_token,
            csrf_token=_header(request.headers, "x-csrf-token"),
            require_csrf=require_csrf,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return actor, session_token

    def _session_cookie(self, token: str, expires_at: datetime) -> str:
        max_age = max(0, int((expires_at - datetime.now(expires_at.tzinfo)).total_seconds()))
        parts = [
            f"{self._cookie_name}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={max_age}",
        ]
        if self._secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    def _clear_session_cookie(self) -> str:
        parts = [
            f"{self._cookie_name}=",
            "Path=/",
            "HttpOnly",
            "SameSite=Lax",
            "Max-Age=0",
        ]
        if self._secure_cookie:
            parts.append("Secure")
        return "; ".join(parts)

    @classmethod
    def _authentication_error(
        cls,
        error: AuthenticationError,
        request_id: str,
        correlation_id: str,
    ) -> HTTPResponse:
        if error.failure is AuthenticationFailure.RATE_LIMITED:
            return cls._error(
                status=429,
                code="rate_limited",
                message="authentication rate limit exceeded",
                request_id=request_id,
                correlation_id=correlation_id,
            )
        return cls._error(
            status=401,
            code="unauthorized",
            message="authentication required or credential is invalid",
            request_id=request_id,
            correlation_id=correlation_id,
            authenticate=True,
        )

    @classmethod
    def _error(
        cls,
        *,
        status: int,
        code: str,
        message: str,
        request_id: str,
        correlation_id: str,
        authenticate: bool = False,
    ) -> HTTPResponse:
        payload = APIError(
            code=code,
            message=message,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        headers = {
            "content-type": "application/json",
            "x-request-id": request_id,
            "x-correlation-id": correlation_id,
            "x-api-version": API_VERSION,
        }
        if authenticate:
            headers["www-authenticate"] = 'Bearer realm="ai-multi-agent-platform"'
        return HTTPResponse(status=status, body=payload.to_json(), headers=headers)


def _with_request_ids(
    request: HTTPRequest,
    request_id: str,
    correlation_id: str,
) -> HTTPRequest:
    headers = dict(request.headers)
    headers["x-request-id"] = request_id
    headers["x-correlation-id"] = correlation_id
    return HTTPRequest(
        method=request.method,
        path=request.path,
        headers=headers,
        query=request.query,
        body=request.body,
    )


def _with_authenticated_actor(
    request: HTTPRequest,
    actor: AuthenticatedActor,
    *,
    request_id: str,
    correlation_id: str,
) -> HTTPRequest:
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.casefold()
        not in {
            "x-principal-ref",
            "x-owner-id",
            "x-owner-type",
            "x-authenticated-actor",
            "x-request-id",
            "x-correlation-id",
        }
    }
    headers["x-request-id"] = request_id
    headers["x-correlation-id"] = correlation_id
    headers["x-principal-ref"] = actor.identity.actor_id
    if actor.identity.actor_type.value == "human":
        headers["x-owner-type"] = "user"
        headers["x-owner-id"] = actor.identity.actor_id
    else:
        headers["x-owner-type"] = "service"
        headers["x-owner-id"] = actor.identity.actor_id
    return HTTPRequest(
        method=request.method,
        path=request.path,
        headers=headers,
        query=request.query,
        body=request.body,
    )


def _public_route(method: str, relative: str) -> bool:
    if method == "GET" and relative in {"", "/", "/health", "/readiness", "/openapi.json"}:
        return True
    return method == "POST" and relative in {"/auth/login", "/auth/bootstrap-admin"}


def _relative_path(path: str) -> str:
    prefix = f"/api/{API_VERSION}"
    if path == prefix:
        return ""
    if not path.startswith(f"{prefix}/"):
        raise APIException(status=404, code="not_found", message="route not found")
    return path[len(prefix) :]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.casefold() == name.casefold():
            return value
    return None


def _cookies(header: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for pair in header.split(";"):
        name, separator, value = pair.strip().partition("=")
        if separator and name:
            values[name] = value
    return values


def _required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_datetime(value: JsonValue | None) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("expires_at must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    return parsed


def _payload_digest(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _augment_authentication_openapi(
    specification: dict[str, Any],
    *,
    cookie_name: str,
) -> dict[str, Any]:
    document = dict(specification)
    paths = dict(document.get("paths", {}))
    components = dict(document.get("components", {}))
    security_schemes = dict(components.get("securitySchemes", {}))
    security_schemes.update(
        {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "description": "Opaque personal, service, worker, automation or integration token.",
            },
            "sessionCookie": {
                "type": "apiKey",
                "in": "cookie",
                "name": cookie_name,
                "description": "Opaque HttpOnly browser session cookie.",
            },
        }
    )
    components["securitySchemes"] = security_schemes
    document["components"] = components
    document["security"] = [{"bearerAuth": []}, {"sessionCookie": []}]

    csrf_parameter = {
        "name": "X-CSRF-Token",
        "in": "header",
        "required": False,
        "description": "Required for state-changing browser-session requests; not used by bearer clients.",
        "schema": {"type": "string"},
    }
    auth_paths: dict[str, Any] = {
        f"/api/{API_VERSION}/auth/bootstrap-admin": {
            "post": _auth_operation(
                "bootstrapFirstAdmin",
                "Create the first local human identity in an empty authentication store.",
                public=True,
                request_fields=("username", "password"),
                status="201",
            )
        },
        f"/api/{API_VERSION}/auth/login": {
            "post": _auth_operation(
                "loginLocalUser",
                "Authenticate a local user and create a browser session.",
                public=True,
                request_fields=("username", "password"),
            )
        },
        f"/api/{API_VERSION}/auth/me": {
            "get": _auth_operation("getAuthenticatedActor", "Return the canonical authenticated actor.")
        },
        f"/api/{API_VERSION}/auth/logout": {
            "post": _auth_operation(
                "logoutBrowserSession",
                "Revoke the current browser session.",
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{API_VERSION}/auth/session:renew": {
            "post": _auth_operation(
                "renewBrowserSession",
                "Rotate the current browser session and CSRF token.",
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{API_VERSION}/auth/sessions": {
            "get": _auth_operation("listBrowserSessions", "List browser sessions for the current user.")
        },
        f"/api/{API_VERSION}/auth/sessions/{{session_id}}:revoke": {
            "post": _auth_operation(
                "revokeBrowserSession",
                "Revoke one browser session owned by the current user.",
                parameters=(
                    {
                        "name": "session_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    csrf_parameter,
                ),
            )
        },
        f"/api/{API_VERSION}/auth/password:change": {
            "post": _auth_operation(
                "changeLocalPassword",
                "Change the current local user's password and invalidate sessions.",
                request_fields=("current_password", "new_password"),
                parameters=(csrf_parameter,),
            )
        },
        f"/api/{API_VERSION}/auth/credentials": {
            "get": _auth_operation(
                "listPersonalCredentials",
                "List safe personal credential metadata after #15 authorization.",
            ),
            "post": _auth_operation(
                "createPersonalCredential",
                "Issue a personal credential once after #15 manage_credentials authorization.",
                request_fields=("purpose",),
                status="201",
                parameters=(csrf_parameter,),
            ),
        },
        f"/api/{API_VERSION}/auth/credentials/{{credential_id}}:revoke": {
            "post": _auth_operation(
                "revokePersonalCredential",
                "Revoke a personal credential after #15 manage_credentials authorization.",
                parameters=(
                    {
                        "name": "credential_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    csrf_parameter,
                ),
            )
        },
    }
    paths.update(auth_paths)
    document["paths"] = paths
    return document


def _auth_operation(
    operation_id: str,
    description: str,
    *,
    public: bool = False,
    request_fields: tuple[str, ...] = (),
    status: str = "200",
    parameters: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "operationId": operation_id,
        "description": description,
        "responses": {
            status: {"description": "Authentication operation result"},
            "400": {"$ref": "#/components/responses/Error"},
            "401": {"$ref": "#/components/responses/Error"},
            "403": {"$ref": "#/components/responses/Error"},
            "429": {"$ref": "#/components/responses/Error"},
        },
    }
    if public:
        operation["security"] = []
    if parameters:
        operation["parameters"] = list(parameters)
    if request_fields:
        properties = {field: {"type": "string"} for field in request_fields}
        operation["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": list(request_fields),
                        "additionalProperties": True,
                    }
                }
            },
        }
    return operation
