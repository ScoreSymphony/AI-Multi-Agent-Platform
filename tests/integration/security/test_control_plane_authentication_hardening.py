"""Migrated under #722; original coverage tracked issue #36."""


# ruff: noqa: F401

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

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
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    AuthorizationAction,
    AuthorizationGate,
    ControlPlaneAuthorizationBridge,
    CredentialKind,
    CredentialScope,
    InMemoryAuthenticationStore,
    InMemoryRequestRateLimiter,
    LocalAuthenticationService,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    ScryptPasswordHasher,
    VerifiedExternalIdentity,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

NOW = datetime(2026, 9, 3, 18, 30, tzinfo=UTC)


PASSWORD = "correct horse battery staple"


def _service(
    *,
    store: InMemoryAuthenticationStore | None = None,
    max_requests: int = 600,
    audit: list[AuthenticationAuditRecord] | None = None,
) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        store=store,
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        request_rate_limiter=InMemoryRequestRateLimiter(max_requests=max_requests),
        audit_sink=audit.append if audit is not None else None,
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


class _AuditIdP:
    provider_id = "audit-oidc"

    def verify(self, assertion: str) -> VerifiedExternalIdentity:
        if assertion != "valid-assertion":
            raise ValueError("provider rejected assertion")
        return VerifiedExternalIdentity(
            issuer="https://idp.example",
            subject="audit-subject",
            metadata={"external_role": "administrator"},
        )


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


def _run(awaitable: Any) -> Any:
    return asyncio.run(awaitable)


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


def test_session_renewal_revokes_old_session_and_targeted_revoke_is_deterministic() -> None:
    auth = _service()
    auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

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
    old_cookie = login.headers["set-cookie"].split(";", 1)[0]
    old_csrf = login.body["csrf_token"]

    renewed = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/session:renew",
                headers={"cookie": old_cookie, "x-csrf-token": old_csrf},
            )
        )
    )
    assert renewed.status == 200
    new_cookie = renewed.headers["set-cookie"].split(";", 1)[0]
    new_csrf = renewed.body["csrf_token"]
    assert new_cookie != old_cookie

    old_session = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"cookie": old_cookie},
            )
        )
    )
    assert old_session.status == 401

    sessions = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/sessions",
                headers={"cookie": new_cookie},
            )
        )
    )
    assert sessions.status == 200
    active = [item for item in sessions.body["items"] if item["active"]]
    assert len(active) == 1
    active_session_id = active[0]["id"]

    revoked = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/sessions/{active_session_id}:revoke",
                headers={"cookie": new_cookie, "x-csrf-token": new_csrf},
            )
        )
    )
    assert revoked.status == 200
    assert revoked.body == {"id": active_session_id, "revoked": True}

    revoked_session = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"cookie": new_cookie},
            )
        )
    )
    assert revoked_session.status == 401


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


def test_mobile_pairing_http_qr_completion_listing_and_revocation() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bootstrap = auth.create_personal_access_token(user.user_id, purpose="pairing-admin", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    created = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairing",
                headers={"authorization": f"Bearer {bootstrap.secret}"},
                body={"server_origin": "https://platform.example"},
            )
        )
    )
    assert created.status == 201
    assert created.body["server_origin"] == "https://platform.example"
    assert created.body["secret_display"] == "one_time"
    assert isinstance(created.body["fallback_code"], str)
    parsed_qr = urlsplit(created.body["qr_payload"])
    assert parsed_qr.scheme == "aiagentplatform"
    assert parsed_qr.netloc == "pair"
    query = parse_qs(parsed_qr.query)
    assert query["v"] == ["1"]
    assert query["origin"] == ["https://platform.example"]
    pairing_id = created.body["request_id"]
    pairing_secret = query["secret"][0]

    wrong = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:complete",
                body={"proof": "wrong", "device_name": "Android"},
            )
        )
    )
    assert wrong.status == 401

    completed = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:complete",
                body={
                    "proof": pairing_secret,
                    "device_name": "Android",
                    "device_metadata": {
                        "platform": "android",
                        "device_model": "test-device",
                        "app_version": "0.1.0",
                    },
                },
            )
        )
    )
    assert completed.status == 201
    device_secret = completed.body["secret"]
    credential_id = completed.body["credential_id"]

    me = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"authorization": f"Bearer {device_secret}"},
            )
        )
    )
    assert me.status == 200
    assert me.body["actor_id"] == user.user_id

    replay = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:complete",
                body={"proof": pairing_secret, "device_name": "Replay"},
            )
        )
    )
    assert replay.status == 409
    assert replay.body["code"] == "pairing_already_used"

    listed = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/mobile-devices",
                headers={"authorization": f"Bearer {bootstrap.secret}"},
            )
        )
    )
    assert listed.status == 200
    assert len(listed.body["items"]) == 1
    device = listed.body["items"][0]
    assert device["id"] == credential_id
    assert device["metadata"]["client"] == "mobile"
    assert device["metadata"]["device_name"] == "Android"
    assert "secret" not in repr(device)
    assert pairing_secret not in repr(listed.body)
    assert created.body["fallback_code"] not in repr(listed.body)

    revoked = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-devices/{credential_id}:revoke",
                headers={"authorization": f"Bearer {bootstrap.secret}"},
            )
        )
    )
    assert revoked.status == 200

    rejected = _run(
        http.handle(
            HTTPRequest(
                method="GET",
                path="/api/v1/auth/me",
                headers={"authorization": f"Bearer {device_secret}"},
            )
        )
    )
    assert rejected.status == 401


def test_mobile_pairing_http_rejects_remote_http_and_cross_user_management() -> None:
    auth = _service()
    alice = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bob = auth.create_local_user("bob", PASSWORD, now=NOW)
    alice_token = auth.create_personal_access_token(alice.user_id, purpose="alice", now=NOW)
    bob_token = auth.create_personal_access_token(bob.user_id, purpose="bob", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    insecure = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairing",
                headers={"authorization": f"Bearer {alice_token.secret}"},
                body={"server_origin": "http://platform.example"},
            )
        )
    )
    assert insecure.status == 400
    assert insecure.body["code"] == "invalid_request"

    created = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/auth/mobile-pairing",
                headers={"authorization": f"Bearer {alice_token.secret}"},
                body={"server_origin": "https://platform.example"},
            )
        )
    )
    assert created.status == 201
    pairing_id = created.body["request_id"]

    cross_user_cancel = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:cancel",
                headers={"authorization": f"Bearer {bob_token.secret}"},
            )
        )
    )
    assert cross_user_cancel.status == 404

    cancelled = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:cancel",
                headers={"authorization": f"Bearer {alice_token.secret}"},
            )
        )
    )
    assert cancelled.status == 200

    after_cancel = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/auth/mobile-pairing/{pairing_id}:complete",
                body={
                    "proof": created.body["fallback_code"],
                    "device_name": "Cancelled",
                },
            )
        )
    )
    assert after_cancel.status == 410
    assert after_cancel.body["code"] == "pairing_cancelled"


def test_mobile_pairing_openapi_documents_public_completion_and_device_management() -> None:
    auth = _service()
    http = AuthenticatedControlPlaneHTTP(_PermissiveControlPlane(), auth, secure_cookie=False)

    response = _run(http.handle(HTTPRequest(method="GET", path="/api/v1/openapi.json")))

    assert response.status == 200
    paths = response.body["paths"]
    assert "/api/v1/auth/mobile-pairing" in paths
    completion = paths["/api/v1/auth/mobile-pairing/{request_id}:complete"]["post"]
    assert completion["security"] == []
    assert "/api/v1/auth/mobile-devices" in paths
    assert "/api/v1/auth/mobile-devices/{credential_id}:revoke" in paths
