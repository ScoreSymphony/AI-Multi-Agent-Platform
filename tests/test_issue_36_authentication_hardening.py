from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.security import (
    AuthenticationError,
    AuthenticationFailure,
    AuthorizationAction,
    CredentialScope,
    InMemoryRequestRateLimiter,
    LocalAuthenticationService,
    ResourceType,
    ScryptPasswordHasher,
)

NOW = datetime(2026, 9, 3, 18, 30, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


def _service(*, max_requests: int = 600) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        request_rate_limiter=InMemoryRequestRateLimiter(max_requests=max_requests),
    )


class _PermissiveControlPlane:
    registered_collections: tuple[str, ...] = ()
    registered_commands: tuple[str, ...] = ()

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **_: object,
    ) -> None:
        del context, action, resource_ref

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
                }
            ],
            "next_cursor": None,
            "total": 1,
            "limit": query.limit,
        }


def test_scoped_personal_credential_is_a_restrictive_ceiling_before_15() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bootstrap = auth.create_personal_access_token(user.user_id, purpose="bootstrap", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    created = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/credentials",
                headers={"authorization": f"Bearer {bootstrap.secret}"},
                body={
                    "purpose": "read-only task inspection",
                    "scope": {
                        "actions": [AuthorizationAction.VIEW.value],
                        "resource_types": [ResourceType.TASK.value],
                        "resource_ids": [],
                    },
                },
            )
        )
    )
    assert created.status == 201
    assert created.body["scope"] == {
        "actions": ["view"],
        "resource_types": ["task"],
        "resource_ids": [],
    }
    scoped_secret = created.body["secret"]
    assert isinstance(scoped_secret, str)

    allowed = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {scoped_secret}"},
            )
        )
    )
    assert allowed.status == 200

    denied = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {scoped_secret}"},
                body={"objective": "must not be created"},
            )
        )
    )
    assert denied.status == 403
    assert denied.body["category"] == "authorization"

    listed = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/credentials",
                headers={"authorization": f"Bearer {bootstrap.secret}"},
            )
        )
    )
    assert listed.status == 200
    scoped_items = [item for item in listed.body["items"] if item["id"] == created.body["id"]]
    assert scoped_items[0]["scope"]["actions"] == ["view"]


def test_authenticated_request_rate_limit_hook_returns_429() -> None:
    auth = _service(max_requests=1)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="rate-limit", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    first = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {token.secret}"},
            )
        )
    )
    second = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {token.secret}"},
            )
        )
    )

    assert first.status == 200
    assert second.status == 429
    assert second.body["code"] == "rate_limited"


def test_worker_rotation_revokes_old_secret_and_preserves_scope() -> None:
    auth = _service()
    scope = CredentialScope(
        actions=frozenset({AuthorizationAction.EXECUTE}),
        resource_types=frozenset({ResourceType.WORKER}),
    )
    original = auth.create_worker_credential(
        "worker:gpu-1",
        purpose="remote execution",
        scope=scope,
        now=NOW,
    )

    rotation = auth.rotate_worker_credential(
        "worker:gpu-1",
        original.credential_id,
        now=NOW,
    )
    assert rotation.previous_credential_id == original.credential_id
    assert rotation.replacement.credential_id != original.credential_id
    assert auth.credential_scope(rotation.replacement.credential_id) == scope

    with pytest.raises(AuthenticationError) as old_secret:
        auth.authenticate_bearer(original.secret, now=NOW)
    assert old_secret.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED

    replacement_actor = auth.authenticate_bearer(rotation.replacement.secret, now=NOW)
    assert replacement_actor.identity.actor_id == "worker:gpu-1"
    assert replacement_actor.provider_metadata["credential"]["scope_is_restrictive"] is True

    auth.revoke_compromised_worker_credential(
        "worker:gpu-1",
        rotation.replacement.credential_id,
        now=NOW,
    )
    with pytest.raises(AuthenticationError) as compromised:
        auth.authenticate_bearer(rotation.replacement.secret, now=NOW)
    assert compromised.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED


def test_scoped_credential_openapi_matches_request_contract() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    response = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))

    assert response.status == 200
    credential_post = response.body["paths"]["/api/v1/auth/credentials"]["post"]
    schema = credential_post["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["purpose"]
    assert schema["properties"]["expires_at"]["format"] == "date-time"
    scope_schema = schema["properties"]["scope"]
    assert scope_schema["type"] == "object"
    assert scope_schema["properties"]["actions"]["type"] == "array"
    assert scope_schema["properties"]["resource_types"]["type"] == "array"
    assert scope_schema["properties"]["resource_ids"]["type"] == "array"


def _run(awaitable: object) -> object:
    return asyncio.run(awaitable)
