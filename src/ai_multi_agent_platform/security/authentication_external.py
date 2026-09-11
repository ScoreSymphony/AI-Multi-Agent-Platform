"""Explicit external-identity mapping and authentication."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_models import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    ExternalIdentityMapping,
    IdentityProviderAdapter,
    LocalUserAccount,
)
from .authentication_store import InMemoryAuthenticationStore
from .authentication_tokens import authentication_now
from .authorization import ActorIdentity, ActorType


class UserLookup(Protocol):
    def __call__(self, user_id: str) -> LocalUserAccount: ...


class AccountActiveCheck(Protocol):
    def __call__(self, account: LocalUserAccount) -> None: ...


class AuthenticationExternalIdentityService:
    """Own explicit external identity links and canonical human resolution."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore,
        user: UserLookup,
        require_account_active: AccountActiveCheck,
        audit: AuthenticationAuditEmitter,
    ) -> None:
        self.store = store
        self.user = user
        self.require_account_active = require_account_active
        self.audit = audit

    def link_external_identity(
        self,
        provider_id: str,
        issuer: str,
        subject: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> ExternalIdentityMapping:
        current = authentication_now(now)
        self.user(user_id)
        if not provider_id.strip() or not issuer.strip() or not subject.strip():
            raise ValueError("external identity mapping fields must not be blank")
        key = (provider_id, issuer, subject)
        existing = self.store.external_mappings.get(key)
        if existing is not None and existing.user_id != user_id:
            raise ValueError("external identity is already linked to another canonical user")
        mapping = ExternalIdentityMapping(provider_id, issuer, subject, user_id, current)
        self.store.external_mappings[key] = mapping
        self.audit(
            "auth.external_identity_linked",
            now=current,
            success=True,
            subject_id=user_id,
            metadata={"provider_id": provider_id, "issuer": issuer},
        )
        return mapping

    def authenticate_external(
        self,
        adapter: IdentityProviderAdapter,
        assertion: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = authentication_now(now)
        external = adapter.verify(assertion)
        mapping = self.store.external_mappings.get(
            (adapter.provider_id, external.issuer, external.subject)
        )
        if mapping is None:
            raise AuthenticationError(AuthenticationFailure.EXTERNAL_IDENTITY_UNMAPPED)
        account = self.user(mapping.user_id)
        self.require_account_active(account)
        metadata: dict[str, JsonValue] = {
            "issuer": external.issuer,
            "subject": external.subject,
            "claims": dict(external.metadata),
        }
        return AuthenticatedActor(
            identity=ActorIdentity(mapping.user_id, ActorType.HUMAN),
            method=AuthenticationMethod.EXTERNAL_IDP,
            credential_id=f"external:{adapter.provider_id}",
            authenticated_at=current,
            request_id=request_id,
            correlation_id=correlation_id,
            provider_metadata={adapter.provider_id: metadata},
        )


__all__ = ["AuthenticationExternalIdentityService"]
