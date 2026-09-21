"""Migrated under #722; original coverage tracked issue #36."""


# ruff: noqa: F401

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    HTTPRequest,
)
from ai_multi_agent_platform.control_plane.models import APIException, PageQuery, RequestContext
from ai_multi_agent_platform.security import (
    ActorType,
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    AuthorizationAction,
    InMemoryFailureRateLimiter,
    LocalAuthenticationService,
    ResourceType,
    ScryptPasswordHasher,
    VerifiedExternalIdentity,
)
from ai_multi_agent_platform.security.control_plane_bridge import canonical_control_plane_vocabulary

NOW = datetime(2026, 9, 3, 3, 30, tzinfo=UTC)


PASSWORD = "correct horse battery staple"


NEW_PASSWORD = "new correct horse battery staple"


def _service(*, audit: list[AuthenticationAuditRecord] | None = None) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        audit_sink=audit.append if audit is not None else None,
    )


class _IdP:
    provider_id = "example-oidc"

    def verify(self, assertion: str) -> VerifiedExternalIdentity:
        assert assertion == "valid-assertion"
        return VerifiedExternalIdentity(
            issuer="https://idp.example",
            subject="external-123",
            metadata={"roles": ["external-admin"], "department": "engineering"},
        )


class _EchoControlPlane:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        return {
            "items": [
                {
                    "id": "task_fixture",
                    "type": "task",
                    "principal_ref": context.actor.principal_ref,
                    "owner_type": context.actor.owner_type,
                    "owner_id": context.actor.owner_id,
                    "request_id": context.request_id,
                    "correlation_id": context.correlation_id,
                }
            ],
            "next_cursor": None,
            "total": 1,
            "limit": query.limit,
        }


class _ForbiddenControlPlane:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()

    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        raise APIException(status=403, code="forbidden", message="denied by #15")


class _CredentialAuthorizationControlPlane(_EchoControlPlane):
    def __init__(self, *, deny: bool) -> None:
        self.deny = deny
        self.authorization_calls: list[tuple[str, str, str]] = []

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **_: object,
    ) -> None:
        self.authorization_calls.append((context.actor.principal_ref, action, resource_ref))
        if self.deny:
            raise ContractError(ErrorCode.FORBIDDEN, "denied by #15")


class _StreamControlPlane(_EchoControlPlane):
    def __init__(self) -> None:
        self.stream_context: RequestContext | None = None

    async def subscribe_task_events(
        self,
        context: RequestContext,
        task_id: str,
        *,
        after_event_id: str | None = None,
    ) -> object:
        self.stream_context = context

        async def events() -> object:
            yield {
                "id": "event_fixture",
                "task_id": task_id,
                "after_event_id": after_event_id,
            }

        return events()


async def _asgi_request(
    app: ControlPlaneASGI,
    *,
    path: str,
    headers: dict[str, str],
    method: str = "GET",
    body: bytes = b"",
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    scope: dict[str, object] = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [
            (name.encode("latin-1"), value.encode("latin-1")) for name, value in headers.items()
        ],
    }
    await app(scope, receive, send)
    return messages


def _run(awaitable: object) -> object:
    import asyncio

    return asyncio.run(awaitable)


def test_control_plane_requires_authentication_and_does_not_trust_principal_headers() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="test", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    unauthenticated = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/tasks")))
    assert unauthenticated.status == 401
    assert unauthenticated.body["category"] == "authentication"

    authenticated = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={
                    "authorization": f"Bearer {token.secret}",
                    "x-principal-ref": "user:spoofed",
                    "x-owner-id": "user:spoofed",
                    "x-owner-type": "user",
                },
            )
        )
    )
    assert authenticated.status == 200
    item = authenticated.body["items"][0]
    assert item["principal_ref"] == user.user_id
    assert item["owner_id"] == user.user_id


def test_authenticated_request_ids_are_unique_and_propagated_end_to_end() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="test", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    responses = [
        _run(
            http.handle(
                HTTPRequest(
                    method="GET",
                    path="/api/v1/tasks",
                    headers={"authorization": f"Bearer {token.secret}"},
                )
            )
        )
        for _ in range(2)
    ]

    first, second = responses
    assert first.headers["x-request-id"].startswith("request_")
    assert first.headers["x-request-id"] != second.headers["x-request-id"]
    for response in responses:
        item = response.body["items"][0]
        assert item["request_id"] == response.headers["x-request-id"]
        assert item["correlation_id"] == response.headers["x-correlation-id"]


def test_control_plane_keeps_unauthenticated_401_distinct_from_authorization_403() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="test", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_ForbiddenControlPlane(), auth, secure_cookie=False)

    response = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {token.secret}"},
            )
        )
    )
    assert response.status == 403
    assert response.body["category"] == "authorization"


def test_personal_credential_http_management_requires_canonical_manage_credentials() -> None:
    assert canonical_control_plane_vocabulary("credential:create") == (
        AuthorizationAction.MANAGE_CREDENTIALS,
        ResourceType.SECRET_REFERENCE,
    )

    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="bootstrap-test-token", now=NOW)
    control_plane = _CredentialAuthorizationControlPlane(deny=True)
    http = AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False)

    response = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/credentials",
                headers={"authorization": f"Bearer {token.secret}"},
                body={"purpose": "should-not-be-issued"},
            )
        )
    )

    assert response.status == 403
    assert response.body["category"] == "authorization"
    assert control_plane.authorization_calls == [(user.user_id, "credential:create", user.user_id)]
    assert len(auth.list_credentials(user.user_id)) == 1


def test_authenticated_sse_rejects_spoofed_anonymous_stream_and_projects_canonical_actor() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="stream", now=NOW)
    control_plane = _StreamControlPlane()
    app = ControlPlaneASGI(AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False))

    anonymous_messages = _run(
        _asgi_request(
            app,
            path="/api/v1/tasks/task_fixture/events/stream",
            headers={"x-principal-ref": "user:spoofed"},
        )
    )
    assert anonymous_messages[0]["status"] == 401
    assert control_plane.stream_context is None

    authenticated_messages = _run(
        _asgi_request(
            app,
            path="/api/v1/tasks/task_fixture/events/stream",
            headers={
                "authorization": f"Bearer {token.secret}",
                "x-principal-ref": "user:spoofed",
            },
        )
    )
    assert authenticated_messages[0]["status"] == 200
    assert control_plane.stream_context is not None
    assert control_plane.stream_context.actor.principal_ref == user.user_id


def test_authenticated_openapi_documents_auth_routes_and_security_schemes() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    response = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))

    assert response.status == 200
    specification = response.body
    schemes = specification["components"]["securitySchemes"]
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    assert schemes["sessionCookie"]["name"] == "amp_session"
    assert specification["security"] == [{"bearerAuth": []}, {"sessionCookie": []}]
    assert "/api/v1/auth/login" in specification["paths"]
    assert "/api/v1/auth/credentials" in specification["paths"]
    assert specification["paths"]["/api/v1/auth/login"]["post"]["security"] == []


def test_browser_control_plane_login_csrf_session_listing_and_logout() -> None:
    auth = _service()
    auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    login = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/login",
                body={"username": "alice", "password": PASSWORD},
            )
        )
    )
    assert login.status == 200
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=Lax" in login.headers["set-cookie"]
    cookie = login.headers["set-cookie"].split(";", 1)[0]
    csrf = login.body["csrf_token"]

    sessions = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/sessions",
                headers={"cookie": cookie},
            )
        )
    )
    assert sessions.status == 200
    assert len(sessions.body["items"]) == 1
    assert "token_verifier" not in sessions.body["items"][0]

    rejected_logout = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/logout",
                headers={"cookie": cookie},
            )
        )
    )
    assert rejected_logout.status == 401

    logout = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/logout",
                headers={"cookie": cookie, "x-csrf-token": csrf},
            )
        )
    )
    assert logout.status == 200
    assert "Max-Age=0" in logout.headers["set-cookie"]


def test_authenticated_auth_wrong_method_still_returns_405() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="issue-1330", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)
    headers = {
        "authorization": f"Bearer {token.secret}",
        "x-request-id": "request-1330-authenticated",
        "x-correlation-id": "correlation-1330-authenticated",
    }

    wrong_method = _run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/auth/login", headers=headers))
    )
    assert wrong_method.status == 405
    assert wrong_method.body["code"] == "method_not_allowed"

    unknown = _run(
        http.handle(
            HTTPRequest(method="GET", path="/api/v1/auth/not-a-public-route", headers=headers)
        )
    )
    assert unknown.status == 404
    assert unknown.body["code"] == "not_found"


def test_auth_openapi_does_not_invent_internal_route_ownership() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    response = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))

    assert response.status == 200
    paths = response.body["paths"]
    assert set(paths["/api/v1/auth/login"]) == {"post"}
    assert set(paths["/api/v1/auth/me"]) == {"get"}
    assert set(paths["/api/v1/auth/credentials"]) == {"get", "post"}
    assert "/api/v1/auth/not-a-public-route" not in paths


def test_asgi_auth_route_precedence_matches_direct_http_for_invalid_bodies() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)
    app = ControlPlaneASGI(http)
    headers = {
        "x-request-id": "request-1330-asgi",
        "x-correlation-id": "correlation-1330-asgi",
        "content-type": "application/json",
    }
    cases = (
        ("GET", "/api/v1/auth/login", b"{", 405, "method_not_allowed"),
        ("POST", "/api/v1/auth/me", b"[]", 405, "method_not_allowed"),
        ("GET", "/api/v1/auth/not-a-public-route", b"{", 404, "not_found"),
    )

    for method, path, body, expected_status, expected_code in cases:
        messages = _run(
            _asgi_request(
                app,
                path=path,
                headers=headers,
                method=method,
                body=body,
            )
        )
        start = next(message for message in messages if message["type"] == "http.response.start")
        body_message = next(
            message for message in messages if message["type"] == "http.response.body"
        )
        payload = json.loads(body_message["body"])
        assert start["status"] == expected_status
        assert payload["code"] == expected_code
        assert payload["request_id"] == "request-1330-asgi"
        assert payload["correlation_id"] == "correlation-1330-asgi"
