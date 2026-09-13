"""Migrated under #722; original coverage tracked issue #36."""


# ruff: noqa: F401

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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


def test_authentication_attempts_emit_redacted_audit_records() -> None:
    records: list[AuthenticationAuditRecord] = []
    auth = _service(audit=records)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    login = auth.login(
        "alice",
        PASSWORD,
        now=NOW,
        request_id="request-audit",
        correlation_id="correlation-audit",
    )

    auth.authenticate_session(
        login.session.token,
        csrf_token=login.session.csrf_token,
        require_csrf=True,
        now=NOW,
        correlation_id="correlation-audit",
    )
    with pytest.raises(AuthenticationError) as csrf:
        auth.authenticate_session(
            login.session.token,
            csrf_token="ampc1.invalid.invalid",
            require_csrf=True,
            now=NOW,
            correlation_id="correlation-audit",
        )
    assert csrf.value.failure is AuthenticationFailure.CSRF_FAILED

    personal = auth.create_personal_access_token(user.user_id, purpose="audit", now=NOW)
    auth.authenticate_bearer(personal.secret, now=NOW, correlation_id="correlation-audit")
    auth.revoke_credential(user.user_id, personal.credential_id, now=NOW)
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_bearer(personal.secret, now=NOW, correlation_id="correlation-audit")
    assert revoked.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED

    worker = auth.create_worker_credential("worker:audit", now=NOW)
    auth.authenticate_worker_request(
        worker.secret,
        nonce="audit-nonce",
        issued_at=NOW,
        now=NOW,
        correlation_id="correlation-audit",
    )
    with pytest.raises(AuthenticationError) as replay:
        auth.authenticate_worker_request(
            worker.secret,
            nonce="audit-nonce",
            issued_at=NOW,
            now=NOW,
            correlation_id="correlation-audit",
        )
    assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED

    adapter = _AuditIdP()
    with pytest.raises(AuthenticationError) as unmapped:
        auth.authenticate_external(
            adapter,
            "valid-assertion",
            now=NOW,
            correlation_id="correlation-audit",
        )
    assert unmapped.value.failure is AuthenticationFailure.EXTERNAL_IDENTITY_UNMAPPED
    auth.link_external_identity(
        adapter.provider_id,
        "https://idp.example",
        "audit-subject",
        user.user_id,
        now=NOW,
    )
    auth.authenticate_external(
        adapter,
        "valid-assertion",
        now=NOW,
        correlation_id="correlation-audit",
    )
    with pytest.raises(ValueError):
        auth.authenticate_external(
            adapter,
            "invalid-assertion",
            now=NOW,
            correlation_id="correlation-audit",
        )

    assert any(record.event == "auth.session_created" and record.success for record in records)
    assert any(
        record.event == "auth.session_authentication"
        and not record.success
        and record.metadata.get("failure") == AuthenticationFailure.CSRF_FAILED.value
        for record in records
    )
    assert any(
        record.event == "auth.bearer_authentication"
        and not record.success
        and record.metadata.get("failure") == AuthenticationFailure.CREDENTIAL_REVOKED.value
        for record in records
    )
    assert any(
        record.event == "auth.worker_request_authentication"
        and not record.success
        and record.metadata.get("failure") == AuthenticationFailure.REPLAY_REJECTED.value
        for record in records
    )
    assert any(
        record.event == "auth.external_authentication"
        and record.success
        and record.metadata.get("provider_id") == adapter.provider_id
        for record in records
    )
    assert any(
        record.event == "auth.external_authentication"
        and not record.success
        and record.metadata.get("failure") == "provider_verification_failed"
        for record in records
    )

    serialized = repr(records)
    for secret in (
        PASSWORD,
        login.session.token,
        login.session.csrf_token,
        personal.secret,
        worker.secret,
        "valid-assertion",
        "invalid-assertion",
    ):
        assert secret not in serialized


def test_locked_account_and_automation_integration_credentials() -> None:
    records: list[AuthenticationAuditRecord] = []
    auth = _service(audit=records)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    auth.set_account_locked(user.user_id, True, now=NOW)

    with pytest.raises(AuthenticationError) as locked:
        auth.authenticate_password("alice", PASSWORD, now=NOW)
    assert locked.value.failure is AuthenticationFailure.ACCOUNT_LOCKED
    assert any(
        record.event == "auth.login"
        and not record.success
        and record.metadata.get("failure") == AuthenticationFailure.ACCOUNT_LOCKED.value
        for record in records
    )

    automation = auth.create_credential(
        "automation:nightly",
        ActorType.AUTOMATION,
        CredentialKind.AUTOMATION,
        purpose="scheduled workflow",
        expires_at=NOW + timedelta(minutes=1),
        now=NOW,
    )
    automation_actor = auth.authenticate_bearer(automation.secret, now=NOW)
    assert automation_actor.identity.actor_type is ActorType.AUTOMATION
    assert automation_actor.method is AuthenticationMethod.AUTOMATION_TOKEN
    with pytest.raises(AuthenticationError) as expired:
        auth.authenticate_bearer(automation.secret, now=NOW + timedelta(minutes=1))
    assert expired.value.failure is AuthenticationFailure.CREDENTIAL_EXPIRED

    integration = auth.create_credential(
        "integration:calendar",
        ActorType.INTEGRATION,
        CredentialKind.INTEGRATION,
        purpose="calendar integration",
        now=NOW,
    )
    integration_actor = auth.authenticate_bearer(integration.secret, now=NOW)
    assert integration_actor.identity.actor_type is ActorType.INTEGRATION
    assert integration_actor.method is AuthenticationMethod.INTEGRATION_TOKEN


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
