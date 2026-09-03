from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import (
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    HTTPRequest,
)
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorType,
    AuthenticationError,
    AuthenticationFailure,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    CredentialScope,
    InMemoryAuthenticationStore,
    InMemoryRequestRateLimiter,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

NOW = datetime(2026, 9, 3, 18, 30, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


def _service(
    *,
    store: InMemoryAuthenticationStore | None = None,
    max_requests: int = 600,
) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        store=store,
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


def _real_http(
    *,
    allowed_actions: frozenset[AuthorizationAction],
    scope: CredentialScope,
) -> tuple[AuthenticatedControlPlaneHTTP, str, LocalAuthenticationService]:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(
        user.user_id,
        purpose="issue-36-scope-e2e",
        scope=scope,
        now=NOW,
    )
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=user.user_id,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=allowed_actions,
                resource_types=frozenset({ResourceType.TASK}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=ControlPlaneAuthorizationBridge(gate),
    )
    return (
        AuthenticatedControlPlaneHTTP(control_plane, auth, secure_cookie=False),
        token.secret,
        auth,
    )


def test_credential_scope_denies_even_when_15_policy_allows() -> None:
    http, secret, _ = _real_http(
        allowed_actions=frozenset({AuthorizationAction.VIEW, AuthorizationAction.CREATE}),
        scope=CredentialScope(
            actions=frozenset({AuthorizationAction.VIEW}),
            resource_types=frozenset({ResourceType.TASK}),
        ),
    )

    allowed = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/tasks",
                headers={"authorization": f"Bearer {secret}"},
            )
        )
    )
    assert allowed.status == 200

    denied = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={
                    "authorization": f"Bearer {secret}",
                    "content-type": "application/json",
                    "idempotency-key": "scope-must-deny",
                },
                body={
                    "title": "Blocked",
                    "objective": "#15 scope ceiling must deny this create",
                },
            )
        )
    )
    assert denied.status == 403
    assert denied.body["category"] == "authorization"


def test_15_policy_denies_even_when_credential_scope_allows() -> None:
    http, secret, _ = _real_http(
        allowed_actions=frozenset({AuthorizationAction.VIEW}),
        scope=CredentialScope(
            actions=frozenset({AuthorizationAction.CREATE}),
            resource_types=frozenset({ResourceType.TASK}),
        ),
    )

    denied = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers={
                    "authorization": f"Bearer {secret}",
                    "content-type": "application/json",
                    "idempotency-key": "policy-must-deny",
                },
                body={
                    "title": "Blocked by policy",
                    "objective": "Credential scope must never grant #15 permission",
                },
            )
        )
    )
    assert denied.status == 403
    assert denied.body["category"] == "authorization"


def test_scoped_credential_survives_authentication_service_recreation() -> None:
    store = InMemoryAuthenticationStore()
    first = _service(store=store)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    scope = CredentialScope(
        actions=frozenset({AuthorizationAction.READ}),
        resource_types=frozenset({ResourceType.ARTIFACT}),
        resource_ids=frozenset({"artifact_fixture"}),
    )
    token = first.create_personal_access_token(
        user.user_id,
        purpose="durable scope",
        scope=scope,
        now=NOW,
    )

    recreated = _service(store=store)
    assert recreated.credential_scope(token.credential_id) == scope
    actor = recreated.authenticate_bearer(token.secret, now=NOW)
    assert actor.provider_metadata["credential"]["scope"] == scope.to_json()


def test_corrupt_persisted_scope_fails_closed_during_bearer_authentication() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(
        user.user_id,
        purpose="corrupt-scope-test",
        scope=CredentialScope(actions=frozenset({AuthorizationAction.VIEW})),
        now=NOW,
    )
    stored = auth.store.credentials[token.credential_id]
    auth.store.credentials[token.credential_id] = replace(stored, scope={"actions": []})

    with pytest.raises(AuthenticationError) as exc:
        auth.authenticate_bearer(token.secret, now=NOW)
    assert exc.value.failure is AuthenticationFailure.INVALID_CREDENTIALS


def test_scoped_personal_credential_http_contract_exposes_safe_scope_metadata() -> None:
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
    assert isinstance(created.body["secret"], str)

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
    assert "secret" not in scoped_items[0]


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


def test_scoped_credential_openapi_matches_current_composed_http_contract() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    response = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))

    assert response.status == 200
    assert "x-automation" in response.body
    credential_post = response.body["paths"]["/api/v1/auth/credentials"]["post"]
    schema = credential_post["requestBody"]["content"]["application/json"]["schema"]
    assert schema["required"] == ["purpose"]
    assert schema["properties"]["expires_at"]["format"] == "date-time"
    scope_schema = schema["properties"]["scope"]
    assert scope_schema["type"] == "object"
    assert scope_schema["properties"]["actions"]["type"] == "array"
    assert scope_schema["properties"]["resource_types"]["type"] == "array"
    assert scope_schema["properties"]["resource_ids"]["type"] == "array"


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)
