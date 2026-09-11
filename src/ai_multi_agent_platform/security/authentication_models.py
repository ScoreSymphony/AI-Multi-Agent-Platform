"""Provider-neutral authentication domain records and protocol contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .authorization import ActorIdentity, ActorType
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
    "IssuedCredential",
    "LocalUserAccount",
    "LoginResult",
    "ReplayProtector",
    "SessionGrant",
    "StoredCredential",
    "VerifiedExternalIdentity",
]
