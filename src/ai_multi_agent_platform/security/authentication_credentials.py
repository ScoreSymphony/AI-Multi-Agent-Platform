"""Long-lived bearer credentials and authenticated worker requests."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_models import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    CredentialKind,
    IssuedCredential,
    LocalUserAccount,
    ReplayProtector,
    StoredCredential,
)
from .authentication_store import InMemoryAuthenticationStore
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


class UserLookup(Protocol):
    def __call__(self, user_id: str) -> LocalUserAccount: ...


class AccountActiveCheck(Protocol):
    def __call__(self, account: LocalUserAccount) -> None: ...


class AuthenticationCredentialService:
    """Own bearer credential lifecycle and worker replay authentication."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore,
        replay_protector: ReplayProtector,
        user: UserLookup,
        require_account_active: AccountActiveCheck,
        audit: AuthenticationAuditEmitter,
    ) -> None:
        self.store = store
        self.replay_protector = replay_protector
        self.user = user
        self.require_account_active = require_account_active
        self.audit = audit

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
        current = authentication_now(now)
        if not owner_id.strip() or not purpose.strip():
            raise ValueError("credential owner and purpose must not be blank")
        validate_credential_kind(actor_type, kind)
        validate_actor_reference(owner_id, actor_type)
        if actor_type is ActorType.HUMAN:
            self.require_account_active(self.user(owner_id))
        if expires_at is not None and expires_at <= current:
            raise ValueError("credential expiry must be in the future")
        credential_id = new_id("credential")
        secret = secrets.token_urlsafe(32)
        stored_scope = dict(scope) if scope is not None else unrestricted_credential_scope()
        self.store.credentials[credential_id] = StoredCredential(
            credential_id=credential_id,
            owner_id=owner_id,
            actor_type=actor_type,
            kind=kind,
            purpose=purpose.strip(),
            secret_verifier=secret_verifier(secret),
            created_at=current,
            scope=stored_scope,
            expires_at=expires_at,
        )
        self.audit(
            "auth.credential_created",
            now=current,
            success=True,
            actor_id=owner_id,
            credential_id=credential_id,
            metadata={"kind": kind.value, "purpose": purpose.strip()},
        )
        return IssuedCredential(
            credential_id=credential_id,
            secret=f"amp1.{credential_id}.{secret}",
            expires_at=expires_at,
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
        current = authentication_now(now)
        credential_id, secret = parse_secret(token, "amp1")
        credential = self.store.credentials.get(credential_id)
        if credential is None or not hmac.compare_digest(
            secret_verifier(secret), credential.secret_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if credential.revoked_at is not None:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_REVOKED)
        if credential.expires_at is not None and current >= credential.expires_at:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_EXPIRED)
        if credential.actor_type is ActorType.HUMAN:
            self.require_account_active(self.user(credential.owner_id))
        self.store.credentials[credential_id] = replace(credential, last_used_at=current)
        return self.actor(
            credential.owner_id,
            credential.actor_type,
            method_for_kind(credential.kind),
            credential_id=credential_id,
            authenticated_at=current,
            expires_at=credential.expires_at,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def list_credentials(self, owner_id: str) -> tuple[StoredCredential, ...]:
        return tuple(
            credential
            for credential in self.store.credentials.values()
            if credential.owner_id == owner_id
        )

    def revoke_credential(
        self,
        owner_id: str,
        credential_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = authentication_now(now)
        credential = self.store.credentials.get(credential_id)
        if credential is None or credential.owner_id != owner_id:
            raise KeyError(credential_id)
        if credential.revoked_at is None:
            self.store.credentials[credential_id] = replace(credential, revoked_at=current)
            self.audit(
                "auth.credential_revoked",
                now=current,
                success=True,
                actor_id=owner_id,
                credential_id=credential_id,
            )

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
        current = authentication_now(now)
        actor = self.authenticate_bearer(
            token,
            now=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        return self.authenticate_worker_actor(
            actor,
            nonce=nonce,
            issued_at=issued_at,
            tls_peer_ref=tls_peer_ref,
            now=current,
        )

    def authenticate_worker_actor(
        self,
        actor: AuthenticatedActor,
        *,
        nonce: str,
        issued_at: datetime,
        tls_peer_ref: str | None = None,
        now: datetime | None = None,
    ) -> AuthenticatedActor:
        """Validate replay protection for an already authenticated worker actor."""

        current = authentication_now(now)
        if actor.identity.actor_type is not ActorType.WORKER or actor.credential_id is None:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if not self.replay_protector.accept(
            actor.credential_id,
            nonce,
            issued_at,
            now=current,
        ):
            raise AuthenticationError(AuthenticationFailure.REPLAY_REJECTED)
        worker_metadata: dict[str, JsonValue] = {"nonce": nonce}
        if tls_peer_ref is not None:
            worker_metadata["tls_peer_ref"] = tls_peer_ref
        return replace(actor, provider_metadata={"worker": worker_metadata})

    @staticmethod
    def actor(
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
        return AuthenticatedActor(
            identity=ActorIdentity(actor_id, actor_type),
            method=method,
            credential_id=credential_id,
            authenticated_at=authenticated_at,
            expires_at=expires_at,
            request_id=request_id,
            correlation_id=correlation_id,
            provider_metadata=provider_metadata or {},
        )


__all__ = ["AuthenticationCredentialService"]
