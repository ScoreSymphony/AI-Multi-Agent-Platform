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


def test_mobile_pairing_is_single_use_revocable_and_secret_safe() -> None:
    records: list[AuthenticationAuditRecord] = []
    auth = _service(audit=records)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    challenge = auth.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
        correlation_id="pairing-correlation",
    )

    assert challenge.secret not in repr(auth._mobile_pairings)
    assert challenge.code not in repr(auth._mobile_pairings)
    assert challenge.qr_payload.startswith("aiagentplatform://pair?")
    assert "https%3A%2F%2Fplatform.example" in challenge.qr_payload

    issued = auth.complete_mobile_pairing(
        challenge.request_id,
        challenge.secret,
        device_name="Samu Android",
        device_metadata={
            "platform": "android",
            "device_model": "test-device",
            "app_version": "0.1.0",
        },
        now=NOW + timedelta(seconds=1),
        correlation_id="pairing-correlation",
    )
    stored = auth.store.credentials[issued.credential_id]
    assert stored.metadata["client"] == "mobile"
    assert stored.metadata["device_name"] == "Samu Android"
    assert stored.metadata["server_origin"] == "https://platform.example"
    assert stored.secret_verifier not in issued.secret
    assert auth.authenticate_bearer(
        issued.secret,
        now=NOW + timedelta(seconds=2),
    ).identity.actor_id == user.user_id

    with pytest.raises(AuthenticationError) as replay:
        auth.complete_mobile_pairing(
            challenge.request_id,
            challenge.secret,
            device_name="Replay",
            now=NOW + timedelta(seconds=3),
        )
    assert replay.value.failure is AuthenticationFailure.PAIRING_ALREADY_USED

    auth.revoke_mobile_device(
        user.user_id,
        issued.credential_id,
        now=NOW + timedelta(seconds=4),
    )
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_bearer(issued.secret, now=NOW + timedelta(seconds=5))
    assert revoked.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED

    serialized_audit = repr(records)
    assert challenge.secret not in serialized_audit
    assert challenge.code not in serialized_audit
    assert issued.secret not in serialized_audit


def test_mobile_pairing_fallback_code_expiry_cancel_and_rate_limit() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    fallback = auth.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    issued = auth.complete_mobile_pairing(
        fallback.request_id,
        fallback.code.lower(),
        device_name="Fallback",
        now=NOW + timedelta(seconds=1),
    )
    assert issued.credential_id in {
        item.credential_id for item in auth.list_mobile_devices(user.user_id)
    }

    expired = auth.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    with pytest.raises(AuthenticationError) as expired_error:
        auth.complete_mobile_pairing(
            expired.request_id,
            expired.secret,
            device_name="Expired",
            now=NOW + timedelta(minutes=5),
        )
    assert expired_error.value.failure is AuthenticationFailure.PAIRING_EXPIRED

    cancelled = auth.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    auth.cancel_mobile_pairing(
        user.user_id,
        cancelled.request_id,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(AuthenticationError) as cancelled_error:
        auth.complete_mobile_pairing(
            cancelled.request_id,
            cancelled.secret,
            device_name="Cancelled",
            now=NOW + timedelta(seconds=2),
        )
    assert cancelled_error.value.failure is AuthenticationFailure.PAIRING_CANCELLED

    attacked = auth.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    for offset in range(5):
        with pytest.raises(AuthenticationError) as wrong:
            auth.complete_mobile_pairing(
                attacked.request_id,
                "wrong-proof",
                device_name="Attacker",
                now=NOW + timedelta(seconds=offset),
            )
        assert wrong.value.failure is AuthenticationFailure.INVALID_CREDENTIALS
    with pytest.raises(AuthenticationError) as limited:
        auth.complete_mobile_pairing(
            attacked.request_id,
            "wrong-proof",
            device_name="Attacker",
            now=NOW + timedelta(seconds=6),
        )
    assert limited.value.failure is AuthenticationFailure.RATE_LIMITED


def test_mobile_device_management_is_owner_bound_and_metadata_is_constrained() -> None:
    auth = _service()
    alice = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bob = auth.create_local_user("bob", PASSWORD, now=NOW)
    challenge = auth.create_mobile_pairing_challenge(
        alice.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    issued = auth.complete_mobile_pairing(
        challenge.request_id,
        challenge.secret,
        device_name="Alice phone",
        now=NOW + timedelta(seconds=1),
    )

    with pytest.raises(KeyError):
        auth.revoke_mobile_device(
            bob.user_id,
            issued.credential_id,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ValueError, match="unsupported mobile device metadata"):
        second = auth.create_mobile_pairing_challenge(
            alice.user_id,
            server_origin="https://platform.example",
            now=NOW,
        )
        auth.complete_mobile_pairing(
            second.request_id,
            second.secret,
            device_name="Bad metadata",
            device_metadata={"token": "must-not-be-accepted"},
            now=NOW + timedelta(seconds=1),
        )
