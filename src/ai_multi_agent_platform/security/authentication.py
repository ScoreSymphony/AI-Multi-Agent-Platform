"""Canonical authentication contracts and self-hosted reference implementation.

Authentication establishes identity. Authorization remains owned by the #15 security
boundary and consumes the canonical ``ActorIdentity`` produced here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

from .authorization import ActorIdentity, ActorType, infer_actor_identity
from .redaction import redact_sensitive


class AuthenticationMethod(StrEnum):
    LOCAL_PASSWORD = "local_password"
    BROWSER_SESSION = "browser_session"
    PERSONAL_ACCESS_TOKEN = "personal_access_token"
    SERVICE_TOKEN = "service_token"
    WORKER_TOKEN = "worker_token"
    AUTOMATION_TOKEN = "automation_token"
    INTEGRATION_TOKEN = "integration_token"
    EXTERNAL_IDP = "external_idp"


class CredentialKind(StrEnum):
    PERSONAL = "personal"
    SERVICE = "service"
    WORKER = "worker"
    AUTOMATION = "automation"
    INTEGRATION = "integration"


class AuthenticationFailure(StrEnum):
    INVALID_CREDENTIALS = "invalid_credentials"
    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_LOCKED = "account_locked"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    CREDENTIAL_EXPIRED = "credential_expired"
    CREDENTIAL_REVOKED = "credential_revoked"
    RATE_LIMITED = "rate_limited"
    CSRF_FAILED = "csrf_failed"
    REPLAY_REJECTED = "replay_rejected"
    EXTERNAL_IDENTITY_UNMAPPED = "external_identity_unmapped"


class AuthenticationError(Exception):
    """Authentication failure carrying a stable non-secret reason code."""

    def __init__(
        self, failure: AuthenticationFailure, message: str = "authentication failed"
    ) -> None:
        super().__init__(message)
        self.failure = failure


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    identity: ActorIdentity
    method: AuthenticationMethod
    credential_id: str | None
    authenticated_at: datetime
    expires_at: datetime | None = None
    organization_id: str | None = None
    project_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    provider_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "provider_metadata", MappingProxyType(dict(self.provider_metadata))
        )

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        return self.expires_at is not None and current >= self.expires_at


@dataclass(frozen=True, slots=True)
class LocalUserAccount:
    user_id: str
    username: str
    password_verifier: str
    enabled: bool
    locked: bool
    created_at: datetime
    password_changed_at: datetime


@dataclass(frozen=True, slots=True)
class BrowserSession:
    session_id: str
    user_id: str
    token_verifier: str
    csrf_verifier: str
    created_at: datetime
    authenticated_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    last_seen_at: datetime | None = None

    def active(self, *, now: datetime) -> bool:
        return self.revoked_at is None and now < self.expires_at


@dataclass(frozen=True, slots=True)
class StoredCredential:
    credential_id: str
    owner_id: str
    actor_type: ActorType
    kind: CredentialKind
    purpose: str
    secret_verifier: str
    created_at: datetime
    scope: Mapping[str, JsonValue]
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", MappingProxyType(dict(self.scope)))

    def active(self, *, now: datetime) -> bool:
        return self.revoked_at is None and (self.expires_at is None or now < self.expires_at)


@dataclass(frozen=True, slots=True)
class IssuedCredential:
    credential_id: str
    secret: str
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class SessionGrant:
    session_id: str
    token: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class LoginResult:
    actor: AuthenticatedActor
    session: SessionGrant


@dataclass(frozen=True, slots=True)
class AuthenticationAuditRecord:
    event: str
    occurred_at: datetime
    success: bool
    actor_id: str | None = None
    subject_id: str | None = None
    credential_id: str | None = None
    correlation_id: str | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        safe = redact_sensitive(dict(self.metadata))
        if not isinstance(safe, dict):
            raise TypeError("authentication audit metadata must serialize as an object")
        object.__setattr__(self, "metadata", MappingProxyType(safe))


@dataclass(frozen=True, slots=True)
class VerifiedExternalIdentity:
    issuer: str
    subject: str
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.subject.strip():
            raise ValueError("external issuer and subject must not be blank")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ExternalIdentityMapping:
    provider_id: str
    issuer: str
    subject: str
    user_id: str
    linked_at: datetime


class IdentityProviderAdapter(Protocol):
    @property
    def provider_id(self) -> str: ...

    def verify(self, assertion: str) -> VerifiedExternalIdentity: ...


class AuthenticationRateLimiter(Protocol):
    def allow(self, key: str, *, now: datetime) -> bool: ...

    def record(self, key: str, *, success: bool, now: datetime) -> None: ...


class ReplayProtector(Protocol):
    def accept(
        self,
        credential_id: str,
        nonce: str,
        issued_at: datetime,
        *,
        now: datetime,
    ) -> bool: ...


AuthenticationAuditSink = Callable[[AuthenticationAuditRecord], None]


class ScryptPasswordHasher:
    """Dependency-free memory-hard password verifier using Python/OpenSSL scrypt."""

    def __init__(
        self,
        *,
        n: int = 2**15,
        r: int = 8,
        p: int = 1,
        dklen: int = 32,
        maxmem: int = 64 * 1024 * 1024,
    ) -> None:
        self._n = n
        self._r = r
        self._p = p
        self._dklen = dklen
        self._maxmem = maxmem

    def hash(self, password: str) -> str:
        _validate_password(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=self._n,
            r=self._r,
            p=self._p,
            dklen=self._dklen,
            maxmem=self._maxmem,
        )
        encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
        encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        parameters = f"n={self._n},r={self._r},p={self._p},dklen={self._dklen}"
        return f"scrypt${parameters}${encoded_salt}${encoded_digest}"

    def verify(self, password: str, verifier: str) -> bool:
        try:
            algorithm, parameters, encoded_salt, encoded_digest = verifier.split("$", 3)
            if algorithm != "scrypt":
                return False
            values = _parse_parameters(parameters)
            salt = _decode_base64(encoded_salt)
            expected = _decode_base64(encoded_digest)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=values["n"],
                r=values["r"],
                p=values["p"],
                dklen=values["dklen"],
                maxmem=self._maxmem,
            )
        except (ValueError, KeyError):
            return False
        return hmac.compare_digest(actual, expected)


class InMemoryAuthenticationStore:
    """Deterministic self-hosted/reference store with no retrievable secret material."""

    def __init__(self) -> None:
        self.users: dict[str, LocalUserAccount] = {}
        self.usernames: dict[str, str] = {}
        self.sessions: dict[str, BrowserSession] = {}
        self.credentials: dict[str, StoredCredential] = {}
        self.external_mappings: dict[tuple[str, str, str], ExternalIdentityMapping] = {}

    def add_user(self, account: LocalUserAccount) -> None:
        normalized = _normalize_username(account.username)
        if account.user_id in self.users or normalized in self.usernames:
            raise ValueError("local user already exists")
        self.users[account.user_id] = account
        self.usernames[normalized] = account.user_id

    def update_user(self, account: LocalUserAccount) -> None:
        if account.user_id not in self.users:
            raise KeyError(account.user_id)
        self.users[account.user_id] = account

    def user_by_username(self, username: str) -> LocalUserAccount | None:
        user_id = self.usernames.get(_normalize_username(username))
        return self.users.get(user_id) if user_id is not None else None


class InMemoryFailureRateLimiter:
    """Reference brute-force hook; production deployments may replace it."""

    def __init__(self, *, max_failures: int = 5, window: timedelta = timedelta(minutes=5)) -> None:
        if max_failures < 1 or window <= timedelta(0):
            raise ValueError("rate limiter requires positive limits")
        self._max_failures = max_failures
        self._window = window
        self._failures: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, *, now: datetime) -> bool:
        failures = self._failures[key]
        self._prune(failures, now)
        return len(failures) < self._max_failures

    def record(self, key: str, *, success: bool, now: datetime) -> None:
        failures = self._failures[key]
        self._prune(failures, now)
        if success:
            failures.clear()
        else:
            failures.append(now)

    def _prune(self, failures: deque[datetime], now: datetime) -> None:
        cutoff = now - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()


class InMemoryReplayProtector:
    """Reference request nonce tracker for worker credentials."""

    def __init__(self, *, max_clock_skew: timedelta = timedelta(minutes=5)) -> None:
        if max_clock_skew <= timedelta(0):
            raise ValueError("max_clock_skew must be positive")
        self._max_clock_skew = max_clock_skew
        self._seen: dict[tuple[str, str], datetime] = {}

    def accept(
        self,
        credential_id: str,
        nonce: str,
        issued_at: datetime,
        *,
        now: datetime,
    ) -> bool:
        if not nonce.strip() or abs(now - issued_at) > self._max_clock_skew:
            return False
        self._prune(now)
        key = (credential_id, nonce)
        if key in self._seen:
            return False
        self._seen[key] = max(now, issued_at) + self._max_clock_skew
        return True

    def _prune(self, now: datetime) -> None:
        stale = [key for key, expires_at in self._seen.items() if expires_at <= now]
        for key in stale:
            del self._seen[key]


class LocalAuthenticationService:
    """Self-hosted authentication baseline for users, services and future workers."""

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
        self._dummy_password_verifier = self.password_hasher.hash(secrets.token_urlsafe(32))

    def bootstrap_first_admin(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> LocalUserAccount:
        """Create the first local human identity; this does not grant authorization rights."""

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
        current = _now(now)
        normalized = _normalize_username(username)
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
        self._audit(
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
        current = _now(now)
        rate_key = f"login:{_normalize_username(username)}"
        if not self.rate_limiter.allow(rate_key, now=current):
            self._audit(
                "auth.login",
                now=current,
                success=False,
                correlation_id=correlation_id,
                metadata={"failure": AuthenticationFailure.RATE_LIMITED.value},
            )
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)

        account = self.store.user_by_username(username)
        verifier = (
            account.password_verifier if account is not None else self._dummy_password_verifier
        )
        verified = self.password_hasher.verify(password, verifier)
        success = account is not None and verified
        self.rate_limiter.record(rate_key, success=success, now=current)
        if not success or account is None:
            self._audit(
                "auth.login",
                now=current,
                success=False,
                correlation_id=correlation_id,
                metadata={"failure": AuthenticationFailure.INVALID_CREDENTIALS.value},
            )
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        self._require_account_active(account)
        actor = self._actor(
            account.user_id,
            ActorType.HUMAN,
            AuthenticationMethod.LOCAL_PASSWORD,
            authenticated_at=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
        self._audit(
            "auth.login",
            now=current,
            success=True,
            actor_id=account.user_id,
            correlation_id=correlation_id,
        )
        return actor

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
        current = _now(now)
        account = self._user(user_id)
        self._require_account_active(account)
        session_id = new_id("session")
        raw_secret = secrets.token_urlsafe(32)
        csrf_secret = secrets.token_urlsafe(32)
        expires_at = current + self.session_ttl
        self.store.sessions[session_id] = BrowserSession(
            session_id=session_id,
            user_id=user_id,
            token_verifier=_secret_verifier(raw_secret),
            csrf_verifier=_secret_verifier(csrf_secret),
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
        current = _now(now)
        session_id, secret = _parse_secret(token, "amps1")
        session = self.store.sessions.get(session_id)
        if session is None or not hmac.compare_digest(
            _secret_verifier(secret), session.token_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if session.revoked_at is not None:
            raise AuthenticationError(AuthenticationFailure.SESSION_REVOKED)
        if current >= session.expires_at:
            raise AuthenticationError(AuthenticationFailure.SESSION_EXPIRED)
        account = self._user(session.user_id)
        self._require_account_active(account)
        if require_csrf:
            if csrf_token is None:
                raise AuthenticationError(AuthenticationFailure.CSRF_FAILED)
            csrf_session_id, csrf_secret = _parse_secret(csrf_token, "ampc1")
            if csrf_session_id != session.session_id or not hmac.compare_digest(
                _secret_verifier(csrf_secret), session.csrf_verifier
            ):
                raise AuthenticationError(AuthenticationFailure.CSRF_FAILED)
        self.store.sessions[session_id] = replace(session, last_seen_at=current)
        return self._actor(
            session.user_id,
            ActorType.HUMAN,
            AuthenticationMethod.BROWSER_SESSION,
            credential_id=session.session_id,
            authenticated_at=session.authenticated_at,
            expires_at=session.expires_at,
            request_id=request_id,
            correlation_id=correlation_id,
        )

    def logout(self, token: str, *, now: datetime | None = None) -> None:
        current = _now(now)
        session_id, secret = _parse_secret(token, "amps1")
        session = self.store.sessions.get(session_id)
        if session is None or not hmac.compare_digest(
            _secret_verifier(secret), session.token_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if session.revoked_at is None:
            self.store.sessions[session_id] = replace(session, revoked_at=current)
            self._audit(
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
        current = _now(now)
        session = self.store.sessions.get(session_id)
        if session is None or session.user_id != user_id:
            raise KeyError(session_id)
        if session.revoked_at is None:
            self.store.sessions[session_id] = replace(session, revoked_at=current)
            self._audit(
                "auth.session_revoked",
                now=current,
                success=True,
                actor_id=user_id,
                credential_id=session_id,
            )

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
    ) -> None:
        current = _now(now)
        account = self._user(user_id)
        self._require_account_active(account)
        if not self.password_hasher.verify(current_password, account.password_verifier):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        updated = replace(
            account,
            password_verifier=self.password_hasher.hash(new_password),
            password_changed_at=current,
        )
        self.store.update_user(updated)
        if invalidate_sessions:
            self._revoke_user_sessions(user_id, now=current)
        self._audit("auth.password_changed", now=current, success=True, actor_id=user_id)

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

        if not operator_ref.strip():
            raise ValueError("operator_ref must not be blank")
        current = _now(now)
        account = self._user(user_id)
        updated = replace(
            account,
            password_verifier=self.password_hasher.hash(new_password),
            password_changed_at=current,
        )
        self.store.update_user(updated)
        if invalidate_sessions:
            self._revoke_user_sessions(user_id, now=current)
        self._audit(
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
    ) -> None:
        current = _now(now)
        account = self._user(user_id)
        self.store.update_user(replace(account, enabled=enabled))
        if not enabled:
            self._revoke_user_sessions(user_id, now=current)
        self._audit(
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
    ) -> None:
        current = _now(now)
        account = self._user(user_id)
        self.store.update_user(replace(account, locked=locked))
        if locked:
            self._revoke_user_sessions(user_id, now=current)
        self._audit(
            "auth.account_locked_changed",
            now=current,
            success=True,
            subject_id=user_id,
            metadata={"locked": locked},
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
        current = _now(now)
        if not owner_id.strip() or not purpose.strip():
            raise ValueError("credential owner and purpose must not be blank")
        _validate_credential_kind(actor_type, kind)
        _validate_actor_reference(owner_id, actor_type)
        if actor_type is ActorType.HUMAN:
            self._require_account_active(self._user(owner_id))
        if expires_at is not None and expires_at <= current:
            raise ValueError("credential expiry must be in the future")
        credential_id = new_id("credential")
        secret = secrets.token_urlsafe(32)
        stored_scope = dict(scope) if scope is not None else _unrestricted_credential_scope()
        self.store.credentials[credential_id] = StoredCredential(
            credential_id=credential_id,
            owner_id=owner_id,
            actor_type=actor_type,
            kind=kind,
            purpose=purpose.strip(),
            secret_verifier=_secret_verifier(secret),
            created_at=current,
            scope=stored_scope,
            expires_at=expires_at,
        )
        self._audit(
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
        current = _now(now)
        credential_id, secret = _parse_secret(token, "amp1")
        credential = self.store.credentials.get(credential_id)
        if credential is None or not hmac.compare_digest(
            _secret_verifier(secret), credential.secret_verifier
        ):
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if credential.revoked_at is not None:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_REVOKED)
        if credential.expires_at is not None and current >= credential.expires_at:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_EXPIRED)
        if credential.actor_type is ActorType.HUMAN:
            self._require_account_active(self._user(credential.owner_id))
        self.store.credentials[credential_id] = replace(credential, last_used_at=current)
        return self._actor(
            credential.owner_id,
            credential.actor_type,
            _method_for_kind(credential.kind),
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
        current = _now(now)
        credential = self.store.credentials.get(credential_id)
        if credential is None or credential.owner_id != owner_id:
            raise KeyError(credential_id)
        if credential.revoked_at is None:
            self.store.credentials[credential_id] = replace(credential, revoked_at=current)
            self._audit(
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
        current = _now(now)
        actor = self.authenticate_bearer(
            token,
            now=current,
            request_id=request_id,
            correlation_id=correlation_id,
        )
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

    def link_external_identity(
        self,
        provider_id: str,
        issuer: str,
        subject: str,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> ExternalIdentityMapping:
        current = _now(now)
        self._user(user_id)
        if not provider_id.strip() or not issuer.strip() or not subject.strip():
            raise ValueError("external identity mapping fields must not be blank")
        key = (provider_id, issuer, subject)
        existing = self.store.external_mappings.get(key)
        if existing is not None and existing.user_id != user_id:
            raise ValueError("external identity is already linked to another canonical user")
        mapping = ExternalIdentityMapping(provider_id, issuer, subject, user_id, current)
        self.store.external_mappings[key] = mapping
        self._audit(
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
        current = _now(now)
        external = adapter.verify(assertion)
        mapping = self.store.external_mappings.get(
            (adapter.provider_id, external.issuer, external.subject)
        )
        if mapping is None:
            raise AuthenticationError(AuthenticationFailure.EXTERNAL_IDENTITY_UNMAPPED)
        account = self._user(mapping.user_id)
        self._require_account_active(account)
        metadata: dict[str, JsonValue] = {
            "issuer": external.issuer,
            "subject": external.subject,
            "claims": dict(external.metadata),
        }
        return self._actor(
            mapping.user_id,
            ActorType.HUMAN,
            AuthenticationMethod.EXTERNAL_IDP,
            credential_id=f"external:{adapter.provider_id}",
            authenticated_at=current,
            request_id=request_id,
            correlation_id=correlation_id,
            provider_metadata={adapter.provider_id: metadata},
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

    def _user(self, user_id: str) -> LocalUserAccount:
        try:
            return self.store.users[user_id]
        except KeyError as exc:
            raise ValueError(f"unknown local user: {user_id}") from exc

    @staticmethod
    def _require_account_active(account: LocalUserAccount) -> None:
        if not account.enabled:
            raise AuthenticationError(AuthenticationFailure.ACCOUNT_DISABLED)
        if account.locked:
            raise AuthenticationError(AuthenticationFailure.ACCOUNT_LOCKED)

    def _revoke_user_sessions(self, user_id: str, *, now: datetime) -> None:
        for session_id, session in tuple(self.store.sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self.store.sessions[session_id] = replace(session, revoked_at=now)

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
        if self.audit_sink is None:
            return
        self.audit_sink(
            AuthenticationAuditRecord(
                event=event,
                occurred_at=now,
                success=success,
                actor_id=actor_id,
                subject_id=subject_id,
                credential_id=credential_id,
                correlation_id=correlation_id,
                metadata=metadata or {},
            )
        )


def safe_session(session: BrowserSession, *, now: datetime | None = None) -> dict[str, JsonValue]:
    current = _now(now)
    return {
        "id": session.session_id,
        "user_id": session.user_id,
        "created_at": session.created_at.isoformat(),
        "authenticated_at": session.authenticated_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
        "last_seen_at": session.last_seen_at.isoformat() if session.last_seen_at else None,
        "active": session.active(now=current),
    }


def safe_credential(credential: StoredCredential) -> dict[str, JsonValue]:
    return {
        "id": credential.credential_id,
        "owner_id": credential.owner_id,
        "actor_type": credential.actor_type.value,
        "kind": credential.kind.value,
        "purpose": credential.purpose,
        "created_at": credential.created_at.isoformat(),
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
        "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
    }


def safe_actor(actor: AuthenticatedActor) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        namespace: value for namespace, value in actor.provider_metadata.items()
    }
    return {
        "actor_id": actor.identity.actor_id,
        "actor_type": actor.identity.actor_type.value,
        "authentication_method": actor.method.value,
        "credential_id": actor.credential_id,
        "authenticated_at": actor.authenticated_at.isoformat(),
        "expires_at": actor.expires_at.isoformat() if actor.expires_at else None,
        "organization_id": actor.organization_id,
        "project_id": actor.project_id,
        "request_id": actor.request_id,
        "correlation_id": actor.correlation_id,
        "provider_metadata": metadata,
    }


def _now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("authentication timestamps must be timezone-aware")
    return current


def _normalize_username(username: str) -> str:
    return username.strip().casefold()


def _validate_password(password: str) -> None:
    if len(password) < 12:
        raise ValueError("local passwords must contain at least 12 characters")
    if len(password.encode("utf-8")) > 1024:
        raise ValueError("local password is too large")


def _secret_verifier(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _parse_secret(value: str, prefix: str) -> tuple[str, str]:
    try:
        actual_prefix, identifier, secret = value.split(".", 2)
    except ValueError as exc:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS) from exc
    if actual_prefix != prefix or not identifier or not secret:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
    return identifier, secret


def _parse_parameters(value: str) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for item in value.split(","):
        name, raw = item.split("=", 1)
        parsed[name] = int(raw)
    return parsed


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _validate_actor_reference(owner_id: str, actor_type: ActorType) -> None:
    inferred = infer_actor_identity(owner_id).actor_type
    if inferred is not actor_type:
        raise ValueError(
            f"credential owner {owner_id!r} does not encode actor type {actor_type.value!r}"
        )


def _validate_credential_kind(actor_type: ActorType, kind: CredentialKind) -> None:
    expected = {
        CredentialKind.PERSONAL: ActorType.HUMAN,
        CredentialKind.SERVICE: ActorType.SERVICE,
        CredentialKind.WORKER: ActorType.WORKER,
        CredentialKind.AUTOMATION: ActorType.AUTOMATION,
        CredentialKind.INTEGRATION: ActorType.INTEGRATION,
    }[kind]
    if actor_type is not expected:
        raise ValueError(f"credential kind {kind.value!r} requires actor type {expected.value!r}")


def _method_for_kind(kind: CredentialKind) -> AuthenticationMethod:
    return {
        CredentialKind.PERSONAL: AuthenticationMethod.PERSONAL_ACCESS_TOKEN,
        CredentialKind.SERVICE: AuthenticationMethod.SERVICE_TOKEN,
        CredentialKind.WORKER: AuthenticationMethod.WORKER_TOKEN,
        CredentialKind.AUTOMATION: AuthenticationMethod.AUTOMATION_TOKEN,
        CredentialKind.INTEGRATION: AuthenticationMethod.INTEGRATION_TOKEN,
    }[kind]


def _unrestricted_credential_scope() -> dict[str, JsonValue]:
    return {
        "actions": [],
        "resource_types": [],
        "resource_ids": [],
    }
