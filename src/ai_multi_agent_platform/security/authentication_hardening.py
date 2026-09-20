"""hardening composition for scoped credentials and request controls.

The primitives in :mod:`security.authentication` remain provider-neutral. This module
adds the public self-hosted composition used by the platform: credential scopes are a
restrictive upper bound persisted with each credential, worker credentials have an
explicit rotation/compromise flow, authenticated requests expose a replaceable
rate-limit hook, and security-sensitive authentication attempts emit redacted audit hooks.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

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
from .authentication_tokens import secret_verifier
from .authorization import ActorType, AuthorizationAction, ResourceType

_SCOPE_FIELDS = {"actions", "resource_types", "resource_ids"}
_MOBILE_PAIRING_PROTOCOL_VERSION = "1"
_MOBILE_PAIRING_TTL = timedelta(minutes=5)
_MOBILE_PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MOBILE_PAIRING_CODE_LENGTH = 10
_MOBILE_DEVICE_METADATA_FIELDS = frozenset(
    {"platform", "device_model", "os_name", "os_version", "app_version"}
)


@dataclass(frozen=True, slots=True)
class CredentialScope:
    """Credential-local authorization ceiling expressed in canonical  vocabulary.

    Empty dimensions mean "not additionally restricted". A scope can only reduce what
     may allow; it never grants a permission on its own.
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
            raise ValueError("credential scope uses unknown  vocabulary") from exc
        return cls(
            actions=actions,
            resource_types=resource_types,
            resource_ids=frozenset(resource_ids_value),
        )


@dataclass(frozen=True, slots=True)
class IssuedMobilePairingChallenge:
    request_id: str
    server_origin: str
    secret: str
    code: str
    created_at: datetime
    expires_at: datetime
    protocol_version: str = _MOBILE_PAIRING_PROTOCOL_VERSION

    @property
    def qr_payload(self) -> str:
        query = urlencode(
            {
                "v": self.protocol_version,
                "origin": self.server_origin,
                "request_id": self.request_id,
                "secret": self.secret,
            }
        )
        return f"aiagentplatform://pair?{query}"


@dataclass(frozen=True, slots=True)
class _MobilePairingChallenge:
    request_id: str
    user_id: str
    server_origin: str
    secret_verifier: str
    code_verifier: str
    scope: CredentialScope
    created_at: datetime
    expires_at: datetime
    correlation_id: str | None = None
    consumed_at: datetime | None = None
    cancelled_at: datetime | None = None


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
    """Public  self-hosted composition with scoped credentials and complete audit hooks."""

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
        self._mobile_pairings: dict[str, _MobilePairingChallenge] = {}
        self._mobile_pairing_lock = threading.RLock()

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
        metadata: Mapping[str, JsonValue] | None = None,
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
            metadata=metadata,
        )

    def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> IssuedCredential:
        return self.create_credential(
            user_id,
            ActorType.HUMAN,
            CredentialKind.PERSONAL,
            purpose=purpose,
            expires_at=expires_at,
            now=now,
            scope=scope,
            metadata=metadata,
        )

    def create_mobile_pairing_challenge(
        self,
        user_id: str,
        *,
        server_origin: str,
        scope: CredentialScope | None = None,
        ttl: timedelta = _MOBILE_PAIRING_TTL,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> IssuedMobilePairingChallenge:
        current = _current(now)
        if ttl <= timedelta(0):
            raise ValueError("mobile pairing TTL must be positive")
        origin = server_origin.strip()
        if not origin:
            raise ValueError("server_origin must not be blank")
        self._require_account_active(self._user(user_id))
        request_id = new_id("pairing")
        secret = secrets.token_urlsafe(32)
        code = "".join(
            secrets.choice(_MOBILE_PAIRING_CODE_ALPHABET)
            for _ in range(_MOBILE_PAIRING_CODE_LENGTH)
        )
        challenge = _MobilePairingChallenge(
            request_id=request_id,
            user_id=user_id,
            server_origin=origin,
            secret_verifier=secret_verifier(secret),
            code_verifier=secret_verifier(code),
            scope=scope or CredentialScope(),
            created_at=current,
            expires_at=current + ttl,
            correlation_id=correlation_id,
        )
        with self._mobile_pairing_lock:
            self._mobile_pairings[request_id] = challenge
        self._audit(
            "auth.mobile_pairing_created",
            now=current,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={
                "pairing_request_id": request_id,
                "server_origin": origin,
                "protocol_version": _MOBILE_PAIRING_PROTOCOL_VERSION,
            },
        )
        return IssuedMobilePairingChallenge(
            request_id=request_id,
            server_origin=origin,
            secret=secret,
            code=code,
            created_at=current,
            expires_at=challenge.expires_at,
        )

    def complete_mobile_pairing(
        self,
        request_id: str,
        proof: str,
        *,
        device_name: str,
        device_metadata: Mapping[str, JsonValue] | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> IssuedCredential:
        current = _current(now)
        candidate = proof.strip()
        if not candidate:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        with self._mobile_pairing_lock:
            challenge = self._mobile_pairings.get(request_id)
            if challenge is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            if challenge.cancelled_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_CANCELLED)
            if challenge.consumed_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_ALREADY_USED)
            if current >= challenge.expires_at:
                raise AuthenticationError(AuthenticationFailure.PAIRING_EXPIRED)

            rate_key = f"mobile-pairing:{request_id}"
            if not self.rate_limiter.allow(rate_key, now=current):
                self._audit_pairing_failure(
                    challenge,
                    current,
                    correlation_id,
                    AuthenticationFailure.RATE_LIMITED,
                )
                raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)

            secret_matches = hmac.compare_digest(
                secret_verifier(candidate),
                challenge.secret_verifier,
            )
            code_matches = hmac.compare_digest(
                secret_verifier(candidate.upper()),
                challenge.code_verifier,
            )
            accepted = bool(secret_matches | code_matches)
            self.rate_limiter.record(rate_key, success=accepted, now=current)
            if not accepted:
                self._audit_pairing_failure(
                    challenge,
                    current,
                    correlation_id,
                    AuthenticationFailure.INVALID_CREDENTIALS,
                )
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)

            normalized_name, metadata = _mobile_device_metadata(
                device_name,
                device_metadata,
                server_origin=challenge.server_origin,
                pairing_request_id=challenge.request_id,
            )
            issued = self.create_personal_access_token(
                challenge.user_id,
                purpose=f"mobile-device:{normalized_name}",
                now=current,
                scope=challenge.scope,
                metadata=metadata,
            )
            self._mobile_pairings[request_id] = replace(challenge, consumed_at=current)

        self._audit(
            "auth.mobile_pairing_completed",
            now=current,
            success=True,
            actor_id=challenge.user_id,
            credential_id=issued.credential_id,
            correlation_id=correlation_id or challenge.correlation_id,
            metadata={
                "pairing_request_id": challenge.request_id,
                "device_name": normalized_name,
                "server_origin": challenge.server_origin,
            },
        )
        return issued

    def cancel_mobile_pairing(
        self,
        user_id: str,
        request_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = _current(now)
        with self._mobile_pairing_lock:
            challenge = self._mobile_pairings.get(request_id)
            if challenge is None or challenge.user_id != user_id:
                raise KeyError(request_id)
            if challenge.consumed_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_ALREADY_USED)
            if challenge.cancelled_at is None:
                self._mobile_pairings[request_id] = replace(
                    challenge,
                    cancelled_at=current,
                )
        self._audit(
            "auth.mobile_pairing_cancelled",
            now=current,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={"pairing_request_id": request_id},
        )

    def list_mobile_devices(self, user_id: str) -> tuple[StoredCredential, ...]:
        return tuple(
            credential
            for credential in self.list_credentials(user_id)
            if credential.metadata.get("client") == "mobile"
        )

    def rename_mobile_device(
        self,
        user_id: str,
        credential_id: str,
        device_name: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> StoredCredential:
        current = _current(now)
        credential = self._mobile_device_credential(user_id, credential_id)
        normalized_name, _ = _mobile_device_metadata(
            device_name,
            {},
            server_origin=str(credential.metadata.get("server_origin", "")),
            pairing_request_id=str(credential.metadata.get("pairing_request_id", "")),
        )
        metadata = dict(credential.metadata)
        metadata["device_name"] = normalized_name
        updated = replace(
            credential,
            purpose=f"mobile-device:{normalized_name}",
            metadata=metadata,
        )
        self.store.credentials[credential_id] = updated
        self._audit(
            "auth.mobile_device_renamed",
            now=current,
            success=True,
            actor_id=user_id,
            credential_id=credential_id,
            correlation_id=correlation_id,
            metadata={"device_name": normalized_name},
        )
        return updated

    def revoke_mobile_device(
        self,
        user_id: str,
        credential_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = _current(now)
        self._mobile_device_credential(user_id, credential_id)
        self.revoke_credential(user_id, credential_id, now=current)
        self._audit(
            "auth.mobile_device_revoked",
            now=current,
            success=True,
            actor_id=user_id,
            credential_id=credential_id,
            correlation_id=correlation_id,
        )

    def _mobile_device_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> StoredCredential:
        credential = self.store.credentials.get(credential_id)
        if (
            credential is None
            or credential.owner_id != user_id
            or credential.metadata.get("client") != "mobile"
        ):
            raise KeyError(credential_id)
        return credential

    def _audit_pairing_failure(
        self,
        challenge: _MobilePairingChallenge,
        now: datetime,
        correlation_id: str | None,
        failure: AuthenticationFailure,
    ) -> None:
        self._audit(
            "auth.mobile_pairing_failed",
            now=now,
            success=False,
            actor_id=challenge.user_id,
            correlation_id=correlation_id or challenge.correlation_id,
            metadata={
                "pairing_request_id": challenge.request_id,
                "failure": failure.value,
            },
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
        # error-boundary: allow-broad-catch=cleanup reviewed cleanup boundary
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


def _mobile_device_metadata(
    device_name: str,
    raw_metadata: Mapping[str, JsonValue] | None,
    *,
    server_origin: str,
    pairing_request_id: str,
) -> tuple[str, dict[str, JsonValue]]:
    normalized_name = device_name.strip()
    if not normalized_name or len(normalized_name) > 120:
        raise ValueError("device_name must be between 1 and 120 characters")
    metadata: dict[str, JsonValue] = {
        "client": "mobile",
        "device_name": normalized_name,
        "server_origin": server_origin,
        "pairing_request_id": pairing_request_id,
        "protocol_version": _MOBILE_PAIRING_PROTOCOL_VERSION,
    }
    for key, value in dict(raw_metadata or {}).items():
        if key not in _MOBILE_DEVICE_METADATA_FIELDS:
            raise ValueError(f"unsupported mobile device metadata field: {key}")
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"mobile device metadata {key} must be a non-empty short string")
        metadata[key] = value.strip()
    return normalized_name, metadata


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
