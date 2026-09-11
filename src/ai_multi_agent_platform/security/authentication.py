"""Stable authentication façade over responsibility-focused internal services.

Authentication establishes identity. Authorization remains owned by the #15 security
boundary and consumes the canonical ``ActorIdentity`` produced here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication_accounts import AuthenticationAccountService
from .authentication_audit import emit_authentication_audit
from .authentication_credentials import AuthenticationCredentialService
from .authentication_external import AuthenticationExternalIdentityService
from .authentication_models import (
    AuthenticatedActor,
    AuthenticationAuditRecord,
    AuthenticationAuditSink,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    AuthenticationRateLimiter,
    BrowserSession,
    CredentialKind,
    ExternalIdentityMapping,
    IdentityProviderAdapter,
    IssuedCredential,
    LocalUserAccount,
    LoginResult,
    ReplayProtector,
    SessionGrant,
    StoredCredential,
    VerifiedExternalIdentity,
)
from .authentication_passwords import (
    ScryptPasswordHasher,
    decode_base64,
    parse_parameters,
    validate_password,
)
from .authentication_protection import InMemoryFailureRateLimiter, InMemoryReplayProtector
from .authentication_serialization import safe_actor, safe_credential, safe_session
from .authentication_sessions import AuthenticationSessionService
from .authentication_store import InMemoryAuthenticationStore, normalize_username
from .authentication_tokens import (
    authentication_now,
    method_for_kind,
    parse_secret,
    secret_verifier,
    unrestricted_credential_scope,
    validate_actor_reference,
    validate_credential_kind,
)
from .authorization import ActorIdentity, ActorType


class LocalAuthenticationService:
    """Stable self-hosted authentication façade for users, services and workers."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore | None = None,
        password_hasher: ScryptPasswordHasher | None = None,
        rate_limiter: AuthenticationRateLimiter | None = None,
        replay_protector: ReplayProtector | None = None,
        audit_sink: AuthenticationAuditSink | None = None,
        session_ttl: timedelta = timedelta(hours=12),
    ) -> None:
        if session_ttl <= timedelta(0):
            raise ValueError("session_ttl must be positive")
        self.store = store or InMemoryAuthenticationStore()
        self.password_hasher = password_hasher or ScryptPasswordHasher()
        self.rate_limiter = rate_limiter or InMemoryFailureRateLimiter()
        self.replay_protector = replay_protector or InMemoryReplayProtector()
        self.audit_sink = audit_sink
        self.session_ttl = session_ttl
        self._accounts = AuthenticationAccountService(
            store=self.store,
            password_hasher=self.password_hasher,
            rate_limiter=self.rate_limiter,
            audit=self._audit,
        )
        self._dummy_password_verifier = self._accounts.dummy_password_verifier
        self._sessions = AuthenticationSessionService(
            store=self.store,
            session_ttl=self.session_ttl,
            user=self._user,
            require_account_active=self._require_account_active,
            audit=self._audit,
        )
        self._credentials = AuthenticationCredentialService(
            store=self.store,
            replay_protector=self.replay_protector,
            user=self._user,
            require_account_active=self._require_account_active,
            audit=self._audit,
        )
        self._external_identities = AuthenticationExternalIdentityService(
            store=self.store,
            user=self._user,
            require_account_active=self._require_account_active,
            audit=self._audit,
        )

    def bootstrap_first_admin(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> LocalUserAccount:
        return self._accounts.bootstrap_first_admin(
            username,
            password,
            now=now,
            correlation_id=correlation_id,
        )

    def create_local_user(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
        audit_event: str = "auth.user_created",
    ) -> LocalUserAccount:
        return self._accounts.create_local_user(
            username,
            password,
            now=now,
            correlation_id=correlation_id,
            audit_event=audit_event,
        )

    def authenticate_password(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return self._accounts.authenticate_password(
            username,
            password,
            now=now,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def login(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> LoginResult:
        current = _now(now)
        actor = self.authenticate_password(
            username,
            password,
            now=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        session = self.create_browser_session(actor.identity.actor_id, now=current)
        session_actor = replace(
            actor,
            method=AuthenticationMethod.BROWSER_SESSION,
            credential_id=session.session_id,
            expires_at=session.expires_at,
        )
        return LoginResult(session_actor, session)

    def create_browser_session(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> SessionGrant:
        return self._sessions.create_browser_session(user_id, now=now)

    def authenticate_session(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return self._sessions.authenticate_session(
            token,
            csrf_token=csrf_token,
            require_csrf=require_csrf,
            now=now,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def logout(self, token: str, *, now: datetime | None = None) -> None:
        self._sessions.logout(token, now=now)

    def list_sessions(self, user_id: str) -> tuple[BrowserSession, ...]:
        return self._sessions.list_sessions(user_id)

    def revoke_session(
        self,
        user_id: str,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._sessions.revoke_session(user_id, session_id, now=now)

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
    ) -> None:
        self._accounts.change_password(
            user_id,
            current_password,
            new_password,
            now=now,
            invalidate_sessions=invalidate_sessions,
            revoke_sessions=self._revoke_user_sessions,
        )

    def reset_local_password(
        self,
        user_id: str,
        new_password: str,
        *,
        operator_ref: str,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
    ) -> None:
        """Trusted local recovery hook; deliberately not an unauthenticated HTTP endpoint."""

        self._accounts.reset_local_password(
            user_id,
            new_password,
            operator_ref=operator_ref,
            now=now,
            invalidate_sessions=invalidate_sessions,
            revoke_sessions=self._revoke_user_sessions,
        )

    def set_account_enabled(
        self,
        user_id: str,
        enabled: bool,
        *,
        now: datetime | None = None,
    ) -> None:
        self._accounts.set_account_enabled(
            user_id,
            enabled,
            now=now,
            revoke_sessions=self._revoke_user_sessions,
        )

    def set_account_locked(
        self,
        user_id: str,
        locked: bool,
        *,
        now: datetime | None = None,
    ) -> None:
        self._accounts.set_account_locked(
            user_id,
            locked,
            now=now,
            revoke_sessions=self._revoke_user_sessions,
        )

    def create_credential(
        self,
        owner_id: str,
        actor_type: ActorType,
        kind: CredentialKind,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: Mapping[str, JsonValue] | None = None,
    ) -> IssuedCredential:
        return self._credentials.create_credential(
            owner_id,
            actor_type,
            kind,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=scope,
        )

    def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            user_id,
            ActorType.HUMAN,
            CredentialKind.PERSONAL,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
        )

    def create_service_credential(
        self,
        service_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            service_id,
            ActorType.SERVICE,
            CredentialKind.SERVICE,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
        )

    def create_worker_credential(
        self,
        worker_id: str,
        *,
        purpose: str = "worker authentication",
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            worker_id,
            ActorType.WORKER,
            CredentialKind.WORKER,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
        )

    def authenticate_bearer(
        self,
        token: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return self._credentials.authenticate_bearer(
            token,
            now=now,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def list_credentials(self, owner_id: str) -> tuple[StoredCredential, ...]:
        return self._credentials.list_credentials(owner_id)

    def revoke_credential(
        self,
        owner_id: str,
        credential_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        self._credentials.revoke_credential(owner_id, credential_id, now=now)

    def authenticate_worker_request(
        self,
        token: str,
        *,
        nonce: str,
        issued_at: datetime,
        tls_peer_ref: str | None = None,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = _now(now)
        actor = self.authenticate_bearer(
            token,
            now=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return self._credentials.authenticate_worker_actor(
            actor,
            nonce=nonce,
            issued_at=issued_at,
            tls_peer_ref=tls_peer_ref,
            now=current,
        )

    def link_external_identity(
        self,
        provider_id: str,
        issuer: str,
        subject: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> ExternalIdentityMapping:
        return self._external_identities.link_external_identity(
            provider_id,
            issuer,
            subject,
            user_id,
            now=now,
        )

    def authenticate_external(
        self,
        adapter: IdentityProviderAdapter,
        assertion: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return self._external_identities.authenticate_external(
            adapter,
            assertion,
            now=now,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def _actor(
        self,
        actor_id: str,
        actor_type: ActorType,
        method: AuthenticationMethod,
        *,
        authenticated_at: datetime,
        credential_id: str | None = None,
        expires_at: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        provider_metadata: Mapping[str, JsonValue] | None = None,
    ) -> AuthenticatedActor:
        return AuthenticationCredentialService.actor(
            actor_id,
            actor_type,
            method,
            authenticated_at=authenticated_at,
            credential_id=credential_id,
            expires_at=expires_at,
            request_id=request_id,
            correlation_id=correlation_id,
            provider_metadata=provider_metadata,
        )

    def _user(self, user_id: str) -> LocalUserAccount:
        return self._accounts.user(user_id)

    @staticmethod
    def _require_account_active(account: LocalUserAccount) -> None:
        AuthenticationAccountService.require_account_active(account)

    def _revoke_user_sessions(self, user_id: str, *, now: datetime) -> None:
        self._sessions.revoke_user_sessions(user_id, now=now)

    def _audit(
        self,
        event: str,
        *,
        now: datetime,
        success: bool,
        actor_id: str | None = None,
        subject_id: str | None = None,
        credential_id: str | None = None,
        correlation_id: str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        emit_authentication_audit(
            self.audit_sink,
            event,
            now=now,
            success=success,
            actor_id=actor_id,
            subject_id=subject_id,
            credential_id=credential_id,
            correlation_id=correlation_id,
            metadata=metadata,
        )


# Private compatibility seams retained for existing internal callers/tests while ownership lives in
# focused components.
def _now(value: datetime | None) -> datetime:
    return authentication_now(value)


def _normalize_username(username: str) -> str:
    return normalize_username(username)


def _validate_password(password: str) -> None:
    validate_password(password)


def _secret_verifier(secret: str) -> str:
    return secret_verifier(secret)


def _parse_secret(value: str, prefix: str) -> tuple[str, str]:
    return parse_secret(value, prefix)


def _parse_parameters(value: str) -> dict[str, int]:
    return parse_parameters(value)


def _decode_base64(value: str) -> bytes:
    return decode_base64(value)


def _validate_actor_reference(owner_id: str, actor_type: ActorType) -> None:
    validate_actor_reference(owner_id, actor_type)


def _validate_credential_kind(actor_type: ActorType, kind: CredentialKind) -> None:
    validate_credential_kind(actor_type, kind)


def _method_for_kind(kind: CredentialKind) -> AuthenticationMethod:
    return method_for_kind(kind)


def _unrestricted_credential_scope() -> dict[str, JsonValue]:
    return unrestricted_credential_scope()


__all__ = [
    "AuthenticatedActor",
    "AuthenticationAuditRecord",
    "AuthenticationAuditSink",
    "AuthenticationError",
    "AuthenticationFailure",
    "AuthenticationMethod",
    "AuthenticationRateLimiter",
    "BrowserSession",
    "CredentialKind",
    "ExternalIdentityMapping",
    "IdentityProviderAdapter",
    "InMemoryAuthenticationStore",
    "InMemoryFailureRateLimiter",
    "InMemoryReplayProtector",
    "IssuedCredential",
    "LocalAuthenticationService",
    "LocalUserAccount",
    "LoginResult",
    "ReplayProtector",
    "ScryptPasswordHasher",
    "SessionGrant",
    "StoredCredential",
    "VerifiedExternalIdentity",
    "safe_actor",
    "safe_credential",
    "safe_session",
]
