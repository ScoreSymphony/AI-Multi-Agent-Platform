"""Browser-session lifecycle and CSRF authentication."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Protocol

from ai_multi_agent_platform.domain import new_id

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_models import (
    AuthenticatedActor,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    BrowserSession,
    LocalUserAccount,
    SessionGrant,
)
from .authentication_store import InMemoryAuthenticationStore
from .authentication_tokens import authentication_now, parse_secret, secret_verifier
from .authorization import ActorIdentity, ActorType


class UserLookup(Protocol):
    def __call__(self, user_id: str) -> LocalUserAccount: ...


class AccountActiveCheck(Protocol):
    def __call__(self, account: LocalUserAccount) -> None: ...


class AuthenticationSessionService:
    """Own browser-session issuance, verification, CSRF, revocation and expiry."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore,
        session_ttl: timedelta,
        user: UserLookup,
        require_account_active: AccountActiveCheck,
        audit: AuthenticationAuditEmitter,
    ) -> None:
        self.store = store
        self.session_ttl = session_ttl
        self.user = user
        self.require_account_active = require_account_active
        self.audit = audit

    def create_browser_session(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> SessionGrant:
        current = authentication_now(now)
        account = self.user(user_id)
        self.require_account_active(account)
        session_id = new_id("session")
        raw_secret = secrets.token_urlsafe(32)
        csrf_secret = secrets.token_urlsafe(32)
        expires_at = current + self.session_ttl
        self.store.sessions[session_id] = BrowserSession(
            session_id=session_id,
            user_id=user_id,
            token_verifier=secret_verifier(raw_secret),
            csrf_verifier=secret_verifier(csrf_secret),
            created_at=current,
            authenticated_at=current,
            expires_at=expires_at,
        )
        return SessionGrant(
            session_id=session_id,
            token=f"amps1.{session_id}.{raw_secret}",
            csrf_token=f"ampc1.{session_id}.{csrf_secret}",
            expires_at=expires_at,
        )

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
        current = authentication_now(now)
        session_id, secret = parse_secret(token, "amps1")
        session = self.store.sessions.get(session_id)
        if session is None or not hmac.compare_digest(
            secret_verifier(secret), session.token_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if session.revoked_at is not None:
            raise AuthenticationError(AuthenticationFailure.SESSION_REVOKED)
        if current >= session.expires_at:
            raise AuthenticationError(AuthenticationFailure.SESSION_EXPIRED)
        account = self.user(session.user_id)
        self.require_account_active(account)
        if require_csrf:
            if csrf_token is None:
                raise AuthenticationError(AuthenticationFailure.CSRF_FAILED)
            csrf_session_id, csrf_secret = parse_secret(csrf_token, "ampc1")
            if csrf_session_id != session.session_id or not hmac.compare_digest(
                secret_verifier(csrf_secret), session.csrf_verifier
            ):
                raise AuthenticationError(AuthenticationFailure.CSRF_FAILED)
        self.store.sessions[session_id] = replace(session, last_seen_at=current)
        return AuthenticatedActor(
            identity=ActorIdentity(session.user_id, ActorType.HUMAN),
            method=AuthenticationMethod.BROWSER_SESSION,
            credential_id=session.session_id,
            authenticated_at=session.authenticated_at,
            expires_at=session.expires_at,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def logout(self, token: str, *, now: datetime | None = None) -> None:
        current = authentication_now(now)
        session_id, secret = parse_secret(token, "amps1")
        session = self.store.sessions.get(session_id)
        if session is None or not hmac.compare_digest(
            secret_verifier(secret), session.token_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if session.revoked_at is None:
            self.store.sessions[session_id] = replace(session, revoked_at=current)
            self.audit(
                "auth.logout",
                now=current,
                success=True,
                actor_id=session.user_id,
                credential_id=session_id,
            )

    def list_sessions(self, user_id: str) -> tuple[BrowserSession, ...]:
        return tuple(
            session for session in self.store.sessions.values() if session.user_id == user_id
        )

    def revoke_session(
        self,
        user_id: str,
        session_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = authentication_now(now)
        session = self.store.sessions.get(session_id)
        if session is None or session.user_id != user_id:
            raise KeyError(session_id)
        if session.revoked_at is None:
            self.store.sessions[session_id] = replace(session, revoked_at=current)
            self.audit(
                "auth.session_revoked",
                now=current,
                success=True,
                actor_id=user_id,
                credential_id=session_id,
            )

    def revoke_user_sessions(self, user_id: str, *, now: datetime) -> None:
        for session_id, session in tuple(self.store.sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self.store.sessions[session_id] = replace(session, revoked_at=now)


__all__ = ["AuthenticationSessionService"]
