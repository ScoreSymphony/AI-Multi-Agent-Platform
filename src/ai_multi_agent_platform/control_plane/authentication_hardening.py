"""Hardened public authentication boundary for the issue #36 Control Plane."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.authentication import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
)
from ai_multi_agent_platform.security.authentication_hardening import (
    CredentialScope,
    LocalAuthenticationService,
    safe_credential_with_scope,
)

from .authentication import (
    AuthenticatedControlPlaneHTTP as _BaseAuthenticatedControlPlaneHTTP,
)
from .authentication import (
    _augment_authentication_openapi,
    _header,
    _optional_datetime,
    _payload_digest,
    _public_route,
    _relative_path,
    _required_string,
    _with_request_ids,
)
from .automation_api import ControlPlaneHTTP as _CurrentControlPlaneHTTP
from .http import HTTPRequest, HTTPResponse, _request_context
from .models import API_VERSION, ActorContext, APIException

_TOKEN_METHODS = {
    AuthenticationMethod.PERSONAL_ACCESS_TOKEN,
    AuthenticationMethod.SERVICE_TOKEN,
    AuthenticationMethod.WORKER_TOKEN,
    AuthenticationMethod.AUTOMATION_TOKEN,
    AuthenticationMethod.INTEGRATION_TOKEN,
}


class AuthenticatedControlPlaneHTTP(_BaseAuthenticatedControlPlaneHTTP):
    """Public #36 boundary over the current composed Control Plane HTTP surface.

    Authentication establishes the canonical actor and transports credential-local scope
    as trusted context. Scope authorization itself is deliberately evaluated by #15.
    """

    def __init__(
        self,
        control_plane: Any,
        authentication: LocalAuthenticationService,
        *,
        cookie_name: str = "amp_session",
        secure_cookie: bool = True,
    ) -> None:
        super().__init__(
            control_plane,
            authentication,
            cookie_name=cookie_name,
            secure_cookie=secure_cookie,
        )
        self._hardened_authentication = authentication
        self._current_http = _CurrentControlPlaneHTTP(control_plane)

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        relative = _relative_path(request.path)
        if relative.startswith("/auth/"):
            response = await super().handle(request)
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

        request_id = _header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _header(request.headers, "x-correlation-id") or request_id
        try:
            actor, _ = self._authenticate_request(
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
            return await self._current_http.handle(trusted)
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

    def _augment_openapi_if_needed(
        self,
        request: HTTPRequest,
        response: HTTPResponse,
    ) -> HTTPResponse:
        if (
            request.method == "GET"
            and request.path == f"/api/{API_VERSION}/openapi.json"
            and response.status == 200
            and isinstance(response.body, dict)
        ):
            return HTTPResponse(
                status=response.status,
                body=_augment_scoped_credentials_openapi(response.body),
                headers=response.headers,
            )
        return response

    def _authenticate_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> tuple[AuthenticatedActor, str | None]:
        actor, session_token = super()._authenticate_request(
            request,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        self._hardened_authentication.check_authenticated_request(actor)
        return actor, session_token

    def prepare_stream_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest | HTTPResponse:
        try:
            actor, _ = self._authenticate_request(
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
        except AuthenticationError as exc:
            return self._authentication_error(exc, request_id, correlation_id)
        except (KeyError, TypeError, ValueError):
            return self._authentication_error(
                AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS),
                request_id,
                correlation_id,
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
        if actor.identity.actor_type.value == "human" and relative == "/auth/credentials":
            user_id = actor.identity.actor_id
            if request.method == "GET":
                await self._authorize_credential_operation(
                    request,
                    actor,
                    action="list",
                    resource_ref=user_id,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                credentials: list[JsonValue] = [
                    safe_credential_with_scope(self._hardened_authentication, item)
                    for item in self._hardened_authentication.list_credentials(user_id)
                ]
                return self._response(
                    200,
                    {"items": credentials},
                    request_id,
                    correlation_id,
                )

            if request.method == "POST":
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
                issued = self._hardened_authentication.create_personal_access_token(
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
                        "expires_at": (
                            issued.expires_at.isoformat() if issued.expires_at else None
                        ),
                        "scope": scope.to_json(),
                        "secret_display": "one_time",
                    },
                    request_id,
                    correlation_id,
                )

        return await super()._authenticated_auth_route(
            request,
            relative,
            actor,
            session_token=session_token,
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
        authorize = getattr(self._control_plane, "_authorize", None)
        if authorize is None:
            raise APIException(
                status=503,
                code="authorization_unavailable",
                message="credential management requires the canonical authorization boundary",
            )
        trusted = self._trusted_request(
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

    def _trusted_request(
        self,
        request: HTTPRequest,
        actor: AuthenticatedActor,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest:
        with_ids = _with_request_ids(request, request_id, correlation_id)
        headers = {
            key: value
            for key, value in with_ids.headers.items()
            if key.casefold()
            not in {
                "x-principal-ref",
                "x-owner-id",
                "x-owner-type",
                "x-authenticated-actor",
            }
        }
        authentication_context: dict[str, JsonValue] = {
            "method": actor.method.value,
            "credential_id": actor.credential_id,
        }
        if actor.method in _TOKEN_METHODS:
            if actor.credential_id is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            scope = self._hardened_authentication.credential_scope(actor.credential_id)
            authentication_context["credential_scope"] = scope.to_json()
        trusted_context: dict[str, JsonValue] = {"authentication": authentication_context}
        owner_type = "user" if actor.identity.actor_type.value == "human" else "service"
        trusted_actor = ActorContext(
            principal_ref=actor.identity.actor_id,
            owner_type=cast(Any, owner_type),
            owner_id=actor.identity.actor_id,
            actor_type=actor.identity.actor_type.value,
            trust_context=trusted_context,
        )
        return HTTPRequest(
            method=request.method,
            path=request.path,
            headers=headers,
            query=request.query,
            body=request.body,
            trusted_actor=trusted_actor,
        )


def _augment_scoped_credentials_openapi(
    specification: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    document = dict(specification)
    raw_paths = document.get("paths")
    if not isinstance(raw_paths, dict):
        return document
    paths = dict(raw_paths)
    path_key = f"/api/{API_VERSION}/auth/credentials"
    raw_credentials_path = paths.get(path_key)
    if not isinstance(raw_credentials_path, dict):
        return document
    credentials_path = dict(raw_credentials_path)
    raw_post = credentials_path.get("post")
    if not isinstance(raw_post, dict):
        return document
    post = dict(raw_post)

    scope_properties: dict[str, JsonValue] = {
        "actions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Canonical #15 AuthorizationAction values.",
        },
        "resource_types": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Canonical #15 ResourceType values.",
        },
        "resource_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional exact resource IDs allowed by this credential ceiling.",
        },
    }
    properties: dict[str, JsonValue] = {
        "purpose": {"type": "string"},
        "expires_at": {"type": "string", "format": "date-time"},
        "scope": {
            "type": "object",
            "description": (
                "Credential-local restrictive ceiling evaluated by #15; it never grants rights."
            ),
            "properties": scope_properties,
            "additionalProperties": False,
        },
    }
    required: list[JsonValue] = ["purpose"]
    request_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": True,
    }
    post["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": request_schema}},
    }
    credentials_path["post"] = post
    paths[path_key] = credentials_path
    document["paths"] = paths
    return document
