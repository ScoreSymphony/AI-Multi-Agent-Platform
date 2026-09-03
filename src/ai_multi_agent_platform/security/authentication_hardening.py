"""Issue #36 hardening composition for scoped credentials and request controls.

The primitives in :mod:`security.authentication` remain provider-neutral. This module
adds the public self-hosted composition used by the platform: credential scopes are a
restrictive upper bound persisted with each credential, worker credentials have an
explicit rotation/compromise flow, authenticated requests expose a replaceable
rate-limit hook, and security-sensitive authentication attempts emit redacted audit hooks.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication import (
    AuthenticatedActor,
    AuthenticationAuditSink,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationRateLimiter,
    CredentialKind,
    IdentityProviderAdapter,
    InMemoryAuthenticationStore,
    IssuedCredential,
    ReplayProtector,
    ScryptPasswordHasher,
    SessionGrant,
    StoredCredential,
    safe_credential,
)
from .authentication import LocalAuthenticationService as _BaseLocalAuthenticationService
from .authorization import ActorType, AuthorizationAction, ResourceType

_SCOPE_FIELDS = {"actions", "resource_types", "resource_ids"}


@dataclass(frozen=True, slots=True)
class CredentialScope:
    """Credential-local authorization ceiling expressed in canonical #15 vocabulary.

    Empty dimensions mean "not additionally restricted". A scope can only reduce what
    #15 may allow; it never grants a permission on its own.
    """

    actions: frozenset[AuthorizationAction] = frozenset()
    resource_types: frozenset[ResourceType] = frozenset()
    resource_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if any(not resource_id.strip() for resource_id in self.resource_ids):
            raise ValueError("credential scope resource_ids must not contain blank values")

    @property
    def restricted(self) -> bool:
        return bool(self.actions or self.resource_types or self.resource_ids)

    def allows(
        self,
        action: AuthorizationAction,
        resource_type: ResourceType,
        resource_id: str | None,
    ) -> bool:
        if self.actions and action not in self.actions:
            return False
        if self.resource_types and resource_type not in self.resource_types:
            return False
        if self.resource_ids:
            return resource_id is not None and resource_id in self.resource_ids
        return True

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "actions": _json_string_list(sorted(action.value for action in self.actions)),
            "resource_types": _json_string_list(
                sorted(resource_type.value for resource_type in self.resource_types)
            ),
            "resource_ids": _json_string_list(sorted(self.resource_ids)),
        }

    @classmethod
    def from_json(cls, value: JsonValue | None) -> CredentialScope:
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise ValueError("credential scope must be an object")
        unknown = set(value) - _SCOPE_FIELDS
        if unknown:
            raise ValueError(f"credential scope contains unsupported fields: {sorted(unknown)!r}")
        missing = _SCOPE_FIELDS - set(value)
        if missing:
            raise ValueError(f"credential scope is missing required fields: {sorted(missing)!r}")

        actions_value = _string_items(value.get("actions"), "actions")
        resource_types_value = _string_items(
            value.get("resource_types"),
            "resource_types",
        )
        resource_ids_value = _string_items(value.get("resource_ids"), "resource_ids")
        try:
            actions = frozenset(AuthorizationAction(item) for item in actions_value)
            resource_types = frozenset(ResourceType(item) for item in resource_types_value)
        except ValueError as exc:
            raise ValueError("credential scope uses unknown #15 vocabulary") from exc
        return cls(
            actions=actions,
            resource_types=resource_types,
            resource_ids=frozenset(resource_ids_value),
        )


@dataclass(frozen=True, slots=True)
class CredentialRotation:
    previous_credential_id: str
    replacement: IssuedCredential
    rotated_at: datetime


class AuthenticationRequestRateLimiter(Protocol):
    """Replaceable hook for authenticated northbound request-rate controls."""

    def allow(self, key: str, *, now: datetime) -> bool: ...

    def record(self, key: str, *, now: datetime) -> None: ...


class InMemoryRequestRateLimiter:
    """Deterministic sliding-window request limiter for the self-hosted baseline."""

    def __init__(
        self,
        *,
        max_requests: int = 600,
        window: timedelta = timedelta(minutes=1),
    ) -> None:
        if max_requests < 1 or window <= timedelta(0):
            raise ValueError("request rate limiter requires positive limits")
        self._max_requests = max_requests
        self._window = window
        self._requests: defaultdict[str, deque[datetime]] = defaultdict(deque)

    def allow(self, key: str, *, now: datetime) -> bool:
        requests = self._requests[key]
        self._prune(requests, now)
        return len(requests) < self._max_requests

    def record(self, key: str, *, now: datetime) -> None:
        requests = self._requests[key]
        self._prune(requests, now)
        requests.append(now)

    def _prune(self, requests: deque[datetime], now: datetime) -> None:
        cutoff = now - self._window
        while requests and requests[0] <= cutoff:
            requests.popleft()


class LocalAuthenticationService(_BaseLocalAuthenticationService):
    """Public #36 self-hosted composition with scoped credentials and complete audit hooks."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore | None = None,
        password_hasher: ScryptPasswordHasher | None = None,
        rate_limiter: AuthenticationRateLimiter | None = None,
        replay_protector: ReplayProtector | None = None,
        audit_sink: AuthenticationAuditSink | None = None,
        session_ttl: timedelta = timedelta(hours=12),
        request_rate_limiter: AuthenticationRequestRateLimiter | None = None,
    ) -> None:
        super().__init__(
            store=store,
            password_hasher=password_hasher,
            rate_limiter=rate_limiter,
            replay_protector=replay_protector,
            audit_sink=audit_sink,
            session_ttl=session_ttl,
        )
        self.request_rate_limiter = request_rate_limiter or InMemoryRequestRateLimiter()

    def authenticate_password(
        self,
        username: str,
        password: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = _current(now)
        try:
            return super().authenticate_password(
                username,
                password,
                now=current,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except AuthenticationError as exc:
            if exc.failure in {
                AuthenticationFailure.ACCOUNT_DISABLED,
                AuthenticationFailure.ACCOUNT_LOCKED,
            }:
                self._audit_authentication_result(
                    "auth.login",
                    now=current,
                    success=False,
                    correlation_id=correlation_id,
                    failure=exc.failure,
                )
            raise

    def create_browser_session(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
    ) -> SessionGrant:
        current = _current(now)
        grant = super().create_browser_session(user_id, now=current)
        self._audit_authentication_result(
            "auth.session_created",
            now=current,
            success=True,
            actor_id=user_id,
            credential_id=grant.session_id,
        )
        return grant

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
        current = _current(now)
        try:
            actor = super().authenticate_session(
                token,
                csrf_token=csrf_token,
                require_csrf=require_csrf,
                now=current,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.session_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=exc.failure,
            )
            raise
        self._audit_authentication_result(
            "auth.session_authentication",
            now=current,
            success=True,
            actor=actor,
        )
        return actor

    def logout(self, token: str, *, now: datetime | None = None) -> None:
        current = _current(now)
        try:
            super().logout(token, now=current)
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.logout",
                now=current,
                success=False,
                failure=exc.failure,
            )
            raise

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        now: datetime | None = None,
        invalidate_sessions: bool = True,
    ) -> None:
        current = _current(now)
        try:
            super().change_password(
                user_id,
                current_password,
                new_password,
                now=current,
                invalidate_sessions=invalidate_sessions,
            )
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.password_changed",
                now=current,
                success=False,
                actor_id=user_id,
                failure=exc.failure,
            )
            raise

    def create_credential(
        self,
        owner_id: str,
        actor_type: ActorType,
        kind: CredentialKind,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | Mapping[str, JsonValue] | None = None,
    ) -> IssuedCredential:
        if isinstance(scope, CredentialScope):
            effective_scope = scope
        elif scope is None:
            effective_scope = CredentialScope()
        else:
            effective_scope = CredentialScope.from_json(dict(scope))
        return super().create_credential(
            owner_id,
            actor_type,
            kind,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=effective_scope.to_json(),
        )

    def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            user_id,
            ActorType.HUMAN,
            CredentialKind.PERSONAL,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=scope,
        )

    def create_service_credential(
        self,
        service_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            service_id,
            ActorType.SERVICE,
            CredentialKind.SERVICE,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=scope,
        )

    def create_worker_credential(
        self,
        worker_id: str,
        *,
        purpose: str = "worker authentication",
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            worker_id,
            ActorType.WORKER,
            CredentialKind.WORKER,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=scope,
        )

    def credential_scope(self, credential_id: str) -> CredentialScope:
        credential = self.store.credentials.get(credential_id)
        if credential is None:
            raise KeyError(credential_id)
        return CredentialScope.from_json(dict(credential.scope))

    def authenticate_bearer(
        self,
        token: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = _current(now)
        try:
            actor = super().authenticate_bearer(
                token,
                now=current,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            actor = self._with_scope_metadata(actor)
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.bearer_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=exc.failure,
            )
            raise
        except (KeyError, ValueError) as exc:
            failure = AuthenticationFailure.INVALID_CREDENTIALS
            self._audit_authentication_result(
                "auth.bearer_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=failure,
            )
            raise AuthenticationError(failure) from exc
        self._audit_authentication_result(
            "auth.bearer_authentication",
            now=current,
            success=True,
            actor=actor,
        )
        return actor

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
        current = _current(now)
        try:
            actor = super().authenticate_worker_request(
                token,
                nonce=nonce,
                issued_at=issued_at,
                tls_peer_ref=tls_peer_ref,
                now=current,
                request_id=request_id,
                correlation_id=correlation_id,
            )
            actor = self._with_scope_metadata(actor)
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.worker_request_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=exc.failure,
            )
            raise
        except (KeyError, ValueError) as exc:
            failure = AuthenticationFailure.INVALID_CREDENTIALS
            self._audit_authentication_result(
                "auth.worker_request_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=failure,
            )
            raise AuthenticationError(failure) from exc
        self._audit_authentication_result(
            "auth.worker_request_authentication",
            now=current,
            success=True,
            actor=actor,
        )
        return actor

    def authenticate_external(
        self,
        adapter: IdentityProviderAdapter,
        assertion: str,
        *,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        current = _current(now)
        try:
            actor = super().authenticate_external(
                adapter,
                assertion,
                now=current,
                request_id=request_id,
                correlation_id=correlation_id,
            )
        except AuthenticationError as exc:
            self._audit_authentication_result(
                "auth.external_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure=exc.failure,
                metadata={"provider_id": adapter.provider_id},
            )
            raise
        except Exception:
            self._audit_authentication_result(
                "auth.external_authentication",
                now=current,
                success=False,
                correlation_id=correlation_id,
                failure="provider_verification_failed",
                metadata={"provider_id": adapter.provider_id},
            )
            raise
        self._audit_authentication_result(
            "auth.external_authentication",
            now=current,
            success=True,
            actor=actor,
            metadata={"provider_id": adapter.provider_id},
        )
        return actor

    def check_authenticated_request(
        self,
        actor: AuthenticatedActor,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _current(now)
        subject = actor.credential_id or actor.identity.actor_id
        rate_key = f"request:{subject}"
        if not self.request_rate_limiter.allow(rate_key, now=current):
            self._audit(
                "auth.request_rate_limited",
                now=current,
                success=False,
                actor_id=actor.identity.actor_id,
                credential_id=actor.credential_id,
                correlation_id=actor.correlation_id,
            )
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
        self.request_rate_limiter.record(rate_key, now=current)

    def rotate_worker_credential(
        self,
        worker_id: str,
        credential_id: str,
        *,
        purpose: str | None = None,
        expires_at: datetime | None = None,
        scope: CredentialScope | None = None,
        now: datetime | None = None,
    ) -> CredentialRotation:
        current = _current(now)
        existing = self._worker_credential(worker_id, credential_id)
        inherited_expiry = (
            existing.expires_at
            if existing.expires_at is not None and existing.expires_at > current
            else None
        )
        replacement = self.create_worker_credential(
            worker_id,
            purpose=purpose or existing.purpose,
            expires_at=expires_at if expires_at is not None else inherited_expiry,
            now=current,
            scope=scope if scope is not None else self.credential_scope(credential_id),
        )
        self.revoke_credential(worker_id, credential_id, now=current)
        self._audit(
            "auth.worker_credential_rotated",
            now=current,
            success=True,
            actor_id=worker_id,
            credential_id=replacement.credential_id,
            metadata={"previous_credential_id": credential_id},
        )
        return CredentialRotation(credential_id, replacement, current)

    def revoke_compromised_worker_credential(
        self,
        worker_id: str,
        credential_id: str,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _current(now)
        self._worker_credential(worker_id, credential_id)
        self.revoke_credential(worker_id, credential_id, now=current)
        self._audit(
            "auth.worker_credential_compromised",
            now=current,
            success=True,
            actor_id=worker_id,
            credential_id=credential_id,
        )

    def _worker_credential(self, worker_id: str, credential_id: str) -> StoredCredential:
        credential = self.store.credentials.get(credential_id)
        if (
            credential is None
            or credential.owner_id != worker_id
            or credential.actor_type is not ActorType.WORKER
            or credential.kind is not CredentialKind.WORKER
        ):
            raise KeyError(credential_id)
        return credential

    def _with_scope_metadata(self, actor: AuthenticatedActor) -> AuthenticatedActor:
        if actor.credential_id is None:
            return actor
        metadata: dict[str, JsonValue] = {
            namespace: value for namespace, value in actor.provider_metadata.items()
        }
        scope = self.credential_scope(actor.credential_id)
        metadata["credential"] = {
            "scope": scope.to_json(),
            "scope_is_restrictive": scope.restricted,
        }
        return replace(actor, provider_metadata=metadata)

    def _audit_authentication_result(
        self,
        event: str,
        *,
        now: datetime,
        success: bool,
        actor: AuthenticatedActor | None = None,
        actor_id: str | None = None,
        credential_id: str | None = None,
        correlation_id: str | None = None,
        failure: AuthenticationFailure | str | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> None:
        details: dict[str, JsonValue] = dict(metadata or {})
        if failure is not None:
            details["failure"] = (
                failure.value if isinstance(failure, AuthenticationFailure) else failure
            )
        self._audit(
            event,
            now=now,
            success=success,
            actor_id=actor.identity.actor_id if actor is not None else actor_id,
            credential_id=actor.credential_id if actor is not None else credential_id,
            correlation_id=(
                actor.correlation_id
                if actor is not None and actor.correlation_id is not None
                else correlation_id
            ),
            metadata=details,
        )


def safe_credential_with_scope(
    authentication: LocalAuthenticationService,
    credential: StoredCredential,
) -> dict[str, JsonValue]:
    payload = safe_credential(credential)
    payload["scope"] = authentication.credential_scope(credential.credential_id).to_json()
    return payload


def _json_string_list(values: Iterable[str]) -> list[JsonValue]:
    result: list[JsonValue] = []
    result.extend(values)
    return result


def _string_items(value: JsonValue | None, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"credential scope {field_name} must be a list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"credential scope {field_name} must be a list of strings")
        result.append(item)
    return result


def _current(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("authentication timestamps must be timezone-aware")
    return current
