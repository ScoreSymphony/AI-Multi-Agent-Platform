from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.control_plane import AuthenticatedControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.models import APIException, PageQuery, RequestContext
from ai_multi_agent_platform.security import (
    ActorType,
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    InMemoryFailureRateLimiter,
    LocalAuthenticationService,
    ScryptPasswordHasher,
    VerifiedExternalIdentity,
)

NOW = datetime(2026, 9, 3, 3, 30, tzinfo=UTC)
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "new correct horse battery staple"


def _service(*, audit: list[AuthenticationAuditRecord] | None = None) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        audit_sink=audit.append if audit is not None else None,
    )


def test_local_login_success_failure_and_disabled_account() -> None:
    auth = _service()
    account = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    actor = auth.authenticate_password("alice", PASSWORD, now=NOW)
    assert actor.identity.actor_id == account.user_id
    assert actor.identity.actor_type is ActorType.HUMAN
    assert actor.method is AuthenticationMethod.LOCAL_PASSWORD
    assert account.password_verifier != PASSWORD
    assert account.password_verifier.startswith("scrypt$")

    with pytest.raises(AuthenticationError) as failed:
        auth.authenticate_password("alice", "definitely-wrong", now=NOW)
    assert failed.value.failure is AuthenticationFailure.INVALID_CREDENTIALS

    auth.set_account_enabled(account.user_id, False, now=NOW)
    with pytest.raises(AuthenticationError) as disabled:
        auth.authenticate_password("alice", PASSWORD, now=NOW)
    assert disabled.value.failure is AuthenticationFailure.ACCOUNT_DISABLED


def test_password_change_invalidates_browser_sessions_and_logout_revokes() -> None:
    auth = _service()
    account = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    first = auth.login("alice", PASSWORD, now=NOW)
    second = auth.login("alice", PASSWORD, now=NOW)

    auth.logout(first.session.token, now=NOW + timedelta(minutes=1))
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_session(first.session.token, now=NOW + timedelta(minutes=1))
    assert revoked.value.failure is AuthenticationFailure.SESSION_REVOKED

    auth.change_password(
        account.user_id,
        PASSWORD,
        NEW_PASSWORD,
        now=NOW + timedelta(minutes=2),
    )
    with pytest.raises(AuthenticationError) as invalidated:
        auth.authenticate_session(second.session.token, now=NOW + timedelta(minutes=2))
    assert invalidated.value.failure is AuthenticationFailure.SESSION_REVOKED

    auth.authenticate_password("alice", NEW_PASSWORD, now=NOW + timedelta(minutes=2))


def test_expired_session_and_csrf_validation() -> None:
    auth = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        session_ttl=timedelta(minutes=10),
    )
    auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    login = auth.login("alice", PASSWORD, now=NOW)

    actor = auth.authenticate_session(
        login.session.token,
        csrf_token=login.session.csrf_token,
        require_csrf=True,
        now=NOW + timedelta(minutes=1),
    )
    assert actor.method is AuthenticationMethod.BROWSER_SESSION

    with pytest.raises(AuthenticationError) as csrf:
        auth.authenticate_session(
            login.session.token,
            csrf_token="ampc1.invalid.invalid",
            require_csrf=True,
            now=NOW + timedelta(minutes=1),
        )
    assert csrf.value.failure is AuthenticationFailure.CSRF_FAILED

    with pytest.raises(AuthenticationError) as expired:
        auth.authenticate_session(login.session.token, now=NOW + timedelta(minutes=10))
    assert expired.value.failure is AuthenticationFailure.SESSION_EXPIRED


def test_personal_service_and_worker_credentials_are_hashed_and_revocable() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    personal = auth.create_personal_access_token(
        user.user_id,
        purpose="cli",
        expires_at=NOW + timedelta(days=30),
        now=NOW,
    )
    stored = auth.store.credentials[personal.credential_id]
    assert stored.secret_verifier not in personal.secret
    assert auth.authenticate_bearer(personal.secret, now=NOW).identity.actor_id == user.user_id
    auth.revoke_credential(user.user_id, personal.credential_id, now=NOW)
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_bearer(personal.secret, now=NOW)
    assert revoked.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED

    service = auth.create_service_credential("service:build", purpose="build", now=NOW)
    service_actor = auth.authenticate_bearer(service.secret, now=NOW)
    assert service_actor.identity.actor_type is ActorType.SERVICE

    worker = auth.create_worker_credential("worker:gpu-1", now=NOW)
    worker_actor = auth.authenticate_worker_request(
        worker.secret,
        nonce="nonce-1",
        issued_at=NOW,
        tls_peer_ref="spiffe://example/worker/gpu-1",
        now=NOW,
    )
    assert worker_actor.identity.actor_type is ActorType.WORKER
    assert worker_actor.provider_metadata["worker"]["tls_peer_ref"] == (
        "spiffe://example/worker/gpu-1"
    )
    with pytest.raises(AuthenticationError) as replay:
        auth.authenticate_worker_request(
            worker.secret,
            nonce="nonce-1",
            issued_at=NOW,
            now=NOW,
        )
    assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED


def test_rate_control_hook_blocks_repeated_failed_login() -> None:
    auth = LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        rate_limiter=InMemoryFailureRateLimiter(max_failures=2, window=timedelta(minutes=5)),
    )
    auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    for _ in range(2):
        with pytest.raises(AuthenticationError) as failed:
            auth.authenticate_password("alice", "wrong-password", now=NOW)
        assert failed.value.failure is AuthenticationFailure.INVALID_CREDENTIALS

    with pytest.raises(AuthenticationError) as limited:
        auth.authenticate_password("alice", PASSWORD, now=NOW)
    assert limited.value.failure is AuthenticationFailure.RATE_LIMITED


class _IdP:
    provider_id = "example-oidc"

    def verify(self, assertion: str) -> VerifiedExternalIdentity:
        assert assertion == "valid-assertion"
        return VerifiedExternalIdentity(
            issuer="https://idp.example",
            subject="external-123",
            metadata={"roles": ["external-admin"], "department": "engineering"},
        )


def test_external_identity_requires_explicit_mapping_and_claims_do_not_become_permissions() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    adapter = _IdP()

    with pytest.raises(AuthenticationError) as unmapped:
        auth.authenticate_external(adapter, "valid-assertion", now=NOW)
    assert unmapped.value.failure is AuthenticationFailure.EXTERNAL_IDENTITY_UNMAPPED

    auth.link_external_identity(
        adapter.provider_id,
        "https://idp.example",
        "external-123",
        user.user_id,
        now=NOW,
    )
    actor = auth.authenticate_external(adapter, "valid-assertion", now=NOW)
    assert actor.identity.actor_id == user.user_id
    assert actor.identity.actor_type is ActorType.HUMAN
    assert actor.identity.team_ids == ()
    assert actor.identity.organization_id is None
    assert actor.provider_metadata[adapter.provider_id]["claims"] == {
        "roles": ["external-admin"],
        "department": "engineering",
    }


def test_authentication_audit_redacts_secret_metadata() -> None:
    records: list[AuthenticationAuditRecord] = []
    auth = _service(audit=records)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    auth.create_personal_access_token(user.user_id, purpose="cli", now=NOW)

    assert records
    for record in records:
        serialized = repr(dict(record.metadata))
        assert PASSWORD not in serialized
        assert "amp1." not in serialized

    explicit = AuthenticationAuditRecord(
        event="fixture",
        occurred_at=NOW,
        success=False,
        metadata={"token": "amp1.secret", "password": PASSWORD, "safe": "visible"},
    )
    assert explicit.metadata["token"] == "[REDACTED]"
    assert explicit.metadata["password"] == "[REDACTED]"
    assert explicit.metadata["safe"] == "visible"


class _EchoControlPlane:
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
                }
            ],
            "next_cursor": None,
            "total": 1,
            "limit": query.limit,
        }


class _ForbiddenControlPlane:
    async def list_tasks(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        raise APIException(status=403, code="forbidden", message="denied by #15")


def test_control_plane_requires_authentication_and_does_not_trust_principal_headers() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    token = auth.create_personal_access_token(user.user_id, purpose="test", now=NOW)
    http = AuthenticatedControlPlaneHTTP(_EchoControlPlane(), auth, secure_cookie=False)

    unauthenticated = _run(
        http.handle(HTTPRequest(method="GET", path="/api/v1/tasks"))
    )
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


def _run(awaitable: object) -> object:
    import asyncio

    return asyncio.run(awaitable)
