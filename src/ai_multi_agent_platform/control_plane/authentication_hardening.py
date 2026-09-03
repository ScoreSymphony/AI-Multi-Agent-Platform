"""Hardened public authentication boundary for the issue #36 Control Plane."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.authentication import (
    AuthenticatedActor,
    AuthenticationMethod,
)
from ai_multi_agent_platform.security.authentication_hardening import (
    CredentialScope,
    LocalAuthenticationService,
    safe_credential_with_scope,
)
from ai_multi_agent_platform.security.authorization import (
    AuthorizationAction,
    ResourceType,
)
from ai_multi_agent_platform.security.control_plane_bridge import (
    canonical_control_plane_vocabulary,
)

from .authentication import (
    AuthenticatedControlPlaneHTTP as _BaseAuthenticatedControlPlaneHTTP,
)
from .authentication import _optional_datetime, _relative_path, _required_string
from .http import HTTPRequest, HTTPResponse
from .models import APIException

_TOKEN_METHODS = {
    AuthenticationMethod.PERSONAL_ACCESS_TOKEN,
    AuthenticationMethod.SERVICE_TOKEN,
    AuthenticationMethod.WORKER_TOKEN,
    AuthenticationMethod.AUTOMATION_TOKEN,
    AuthenticationMethod.INTEGRATION_TOKEN,
}


class AuthenticatedControlPlaneHTTP(_BaseAuthenticatedControlPlaneHTTP):
    """Public #36 boundary with request limits and credential-scope ceilings."""

    def __init__(
        self,
        control_plane: object,
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
        self._enforce_credential_scope(request, actor)
        return actor, session_token

    def prepare_stream_request(
        self,
        request: HTTPRequest,
        *,
        request_id: str,
        correlation_id: str,
    ) -> HTTPRequest | HTTPResponse:
        try:
            return super().prepare_stream_request(
                request,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except APIException as exc:
            return self._error_response(exc, request_id, correlation_id)

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

    def _enforce_credential_scope(
        self,
        request: HTTPRequest,
        actor: AuthenticatedActor,
    ) -> None:
        if actor.method not in _TOKEN_METHODS or actor.credential_id is None:
            return
        scope = self._hardened_authentication.credential_scope(actor.credential_id)
        if not scope.restricted:
            return
        target = _authorization_target(request)
        if target is None:
            return
        action, resource_type, resource_id = target
        if scope.allows(action, resource_type, resource_id):
            return
        raise APIException(
            status=403,
            code="forbidden",
            message="credential scope does not permit this operation",
            details={
                "credential_id": actor.credential_id,
                "action": action.value,
                "resource_type": resource_type.value,
            },
        )


def _authorization_target(
    request: HTTPRequest,
) -> tuple[AuthorizationAction, ResourceType, str | None] | None:
    relative = _relative_path(request.path)
    segments = [segment for segment in relative.split("/") if segment]
    if not segments:
        return None

    if segments[0] == "auth":
        if relative == "/auth/me":
            return None
        if len(segments) >= 2 and segments[1] == "credentials":
            verb = "list" if request.method == "GET" else "create"
            resource_id: str | None = "credential:self"
            if len(segments) == 3 and segments[2].endswith(":revoke"):
                verb = "revoke"
                resource_id = segments[2].removesuffix(":revoke")
            action, resource_type = canonical_control_plane_vocabulary(f"credential:{verb}")
            return action, resource_type, resource_id
        return (
            AuthorizationAction.MANAGE_CREDENTIALS,
            ResourceType.SECRET_REFERENCE,
            "authentication:self",
        )

    if segments[0] == "search":
        return AuthorizationAction.READ, ResourceType.GENERIC, "search"

    root = segments[0]
    resource_name = {
        "model-providers": "model-provider",
        "agents": "agent",
        "agent-teams": "agent-team",
        "workers": "worker",
        "integrations": "integration",
    }.get(root, root[:-1] if root.endswith("s") else root)

    if root == "tasks" and len(segments) >= 3:
        task_id = _strip_command(segments[1])
        if segments[2] == "runs":
            if len(segments) == 3:
                action, resource_type = canonical_control_plane_vocabulary("run:list")
                return action, resource_type, task_id
            run_id = _strip_command(segments[3])
            verb = _command(segments[3]) or "read"
            action, resource_type = canonical_control_plane_vocabulary(f"run:{verb}")
            return action, resource_type, run_id
        if segments[2] == "events":
            action, resource_type = canonical_control_plane_vocabulary("task:subscribe")
            return action, resource_type, task_id
        if segments[2] == "timeline":
            action, resource_type = canonical_control_plane_vocabulary("task:read")
            return action, resource_type, task_id

    if len(segments) == 1:
        verb = "list" if request.method == "GET" else "create"
        resource_id = None
    else:
        resource_id = _strip_command(segments[1])
        command = _command(segments[1])
        verb = command or ("read" if request.method == "GET" else "modify")

    action, resource_type = canonical_control_plane_vocabulary(f"{resource_name}:{verb}")
    return action, resource_type, resource_id


def _command(segment: str) -> str | None:
    _, separator, command = segment.partition(":")
    return command if separator and command else None


def _strip_command(segment: str) -> str:
    return segment.split(":", 1)[0]
