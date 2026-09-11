"""Local account and password authentication flows."""

from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from ai_multi_agent_platform.domain import new_id

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_models import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    AuthenticationRateLimiter,
    LocalUserAccount,
)
from .authentication_passwords import ScryptPasswordHasher
from .authentication_store import InMemoryAuthenticationStore, normalize_username
from .authentication_tokens import authentication_now
from .authorization import ActorIdentity, ActorType


class SessionRevoker(Protocol):
    def __call__(self, user_id: str, *, now: datetime) -> None: ...


class AuthenticationAccountService:
    """Own local identities, password verification and account state changes."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore,
        password_hasher: ScryptPasswordHasher,
        rate_limiter: AuthenticationRateLimiter,
        audit: AuthenticationAuditEmitter,
    ) -> None:
        self.store = store
        self.password_hasher = password_hasher
        self.rate_limiter = rate_limiter
        self.audit = audit
        self.dummy_password_verifier = self.password_hasher.hash(secrets.token_urlsafe(32))

    def bootstrap_first_admin(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> LocalUserAccount:
        if self.store.users:
            raise ValueError("first-user bootstrap is available only on an empty account store")
        return self.create_local_user(
            username,
            password,
            now=now,
            correlation_id=correlation_id,
            audit_event="auth.bootstrap_first_admin",
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
        current = authentication_now(now)
        normalized = normalize_username(username)
        if not normalized:
            raise ValueError("username must not be blank")
        account = LocalUserAccount(
            user_id=new_id("user"),
            username=username.strip(),
            password_verifier=self.password_hasher.hash(password),
            enabled=True,
            locked=False,
            created_at=current,
            password_changed_at=current,
        )
        self.store.add_user(account)
        self.audit(
            audit_event,
            now=current,
            success=True,
            actor_id=account.user_id,
            correlation_id=correlation_id,
        )
        return account

    def authenticate_password(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = authentication_now(now)
        rate_key = f"login:{normalize_username(username)}"
        if not self.rate_limiter.allow(rate_key, now=current):
            self.audit(
                "auth.login",
                now=current,
                success=False,
                correlation_id=correlation_id,
                metadata={"failure": AuthenticationFailure.RATE_LIMITED.value},
            )
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)

        account = self.store.user_by_username(username)
        verifier = (
            account.password_verifier if account is not None else self.dummy_password_verifier
        )
        verified = self.password_hasher.verify(password, verifier)
        success = account is not None and verified
        self.rate_limiter.record(rate_key, success=success, now=current)
        if not success or account is None:
            self.audit(
                "auth.login",
                now=current,
                success=False,
                correlation_id=correlation_id,
                metadata={"failure": AuthenticationFailure.INVALID_CREDENTIALS.value},
            )
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        self.require_account_active(account)
        actor = AuthenticatedActor(
            identity=ActorIdentity(account.user_id, ActorType.HUMAN),
            method=AuthenticationMethod.LOCAL_PASSWORD,
            credential_id=None,
            authenticated_at=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        self.audit(
            "auth.login",
            now=current,
            success=True,
            actor_id=account.user_id,
            correlation_id=correlation_id,
        )
        return actor

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
        revoke_sessions: SessionRevoker,
    ) -> None:
        current = authentication_now(now)
        account = self.user(user_id)
        self.require_account_active(account)
        if not self.password_hasher.verify(current_password, account.password_verifier):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        updated = replace(
            account,
            password_verifier=self.password_hasher.hash(new_password),
            password_changed_at=current,
        )
        self.store.update_user(updated)
        if invalidate_sessions:
            revoke_sessions(user_id, now=current)
        self.audit("auth.password_changed", now=current, success=True, actor_id=user_id)

    def reset_local_password(
        self,
        user_id: str,
        new_password: str,
        *,
        operator_ref: str,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
        revoke_sessions: SessionRevoker,
    ) -> None:
        if not operator_ref.strip():
            raise ValueError("operator_ref must not be blank")
        current = authentication_now(now)
        account = self.user(user_id)
        updated = replace(
            account,
            password_verifier=self.password_hasher.hash(new_password),
            password_changed_at=current,
        )
        self.store.update_user(updated)
        if invalidate_sessions:
            revoke_sessions(user_id, now=current)
        self.audit(
            "auth.password_reset",
            now=current,
            success=True,
            actor_id=operator_ref,
            subject_id=user_id,
        )

    def set_account_enabled(
        self,
        user_id: str,
        enabled: bool,
        *,
        now: datetime | None = None,
        revoke_sessions: SessionRevoker,
    ) -> None:
        current = authentication_now(now)
        account = self.user(user_id)
        self.store.update_user(replace(account, enabled=enabled))
        if not enabled:
            revoke_sessions(user_id, now=current)
        self.audit(
            "auth.account_enabled_changed",
            now=current,
            success=True,
            subject_id=user_id,
            metadata={"enabled": enabled},
        )

    def set_account_locked(
        self,
        user_id: str,
        locked: bool,
        *,
        now: datetime | None = None,
        revoke_sessions: SessionRevoker,
    ) -> None:
        current = authentication_now(now)
        account = self.user(user_id)
        self.store.update_user(replace(account, locked=locked))
        if locked:
            revoke_sessions(user_id, now=current)
        self.audit(
            "auth.account_locked_changed",
            now=current,
            success=True,
            subject_id=user_id,
            metadata={"locked": locked},
        )

    def user(self, user_id: str) -> LocalUserAccount:
        try:
            return self.store.users[user_id]
        except KeyError as exc:
            raise ValueError(f"unknown local user: {user_id}") from exc

    @staticmethod
    def require_account_active(account: LocalUserAccount) -> None:
        if not account.enabled:
            raise AuthenticationError(AuthenticationFailure.ACCOUNT_DISABLED)
        if account.locked:
            raise AuthenticationError(AuthenticationFailure.ACCOUNT_LOCKED)


__all__ = ["AuthenticationAccountService", "SessionRevoker"]
