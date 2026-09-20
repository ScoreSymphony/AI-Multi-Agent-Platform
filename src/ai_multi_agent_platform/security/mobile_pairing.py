"""Short-lived one-time mobile device pairing lifecycle."""

from __future__ import annotations

import hmac
import secrets
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import urlencode

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_models import (
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationRateLimiter,
    IssuedCredential,
)
from .authentication_tokens import secret_verifier

MOBILE_PAIRING_PROTOCOL_VERSION = "1"
MOBILE_PAIRING_TTL = timedelta(minutes=5)
_MOBILE_PAIRING_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MOBILE_PAIRING_CODE_LENGTH = 10
_MOBILE_DEVICE_METADATA_FIELDS = frozenset(
    {"platform", "device_model", "os_name", "os_version", "app_version"}
)


class MobileCredentialIssuer(Protocol):
    def __call__(
        self,
        user_id: str,
        *,
        device_name: str,
        scope: Mapping[str, JsonValue],
        metadata: Mapping[str, JsonValue],
        now: datetime,
    ) -> IssuedCredential: ...


@dataclass(frozen=True, slots=True)
class IssuedMobilePairingChallenge:
    request_id: str
    server_origin: str
    secret: str
    code: str
    created_at: datetime
    expires_at: datetime
    protocol_version: str = MOBILE_PAIRING_PROTOCOL_VERSION

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
    scope: Mapping[str, JsonValue]
    created_at: datetime
    expires_at: datetime
    correlation_id: str | None = None
    consumed_at: datetime | None = None
    cancelled_at: datetime | None = None


class MobilePairingManager:
    """Own ephemeral proof material while durable credentials remain in Authentication."""

    def __init__(
        self,
        *,
        rate_limiter: AuthenticationRateLimiter,
        audit: AuthenticationAuditEmitter,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._audit = audit
        self._challenges: dict[str, _MobilePairingChallenge] = {}
        self._lock = threading.RLock()

    def create(
        self,
        user_id: str,
        *,
        server_origin: str,
        scope: Mapping[str, JsonValue],
        now: datetime,
        correlation_id: str | None = None,
        ttl: timedelta = MOBILE_PAIRING_TTL,
    ) -> IssuedMobilePairingChallenge:
        if ttl <= timedelta(0):
            raise ValueError("mobile pairing TTL must be positive")
        origin = server_origin.strip()
        if not origin:
            raise ValueError("server_origin must not be blank")

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
            scope=dict(scope),
            created_at=now,
            expires_at=now + ttl,
            correlation_id=correlation_id,
        )
        with self._lock:
            self._challenges[request_id] = challenge
        self._audit(
            "auth.mobile_pairing_created",
            now=now,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={
                "pairing_request_id": request_id,
                "server_origin": origin,
                "protocol_version": MOBILE_PAIRING_PROTOCOL_VERSION,
            },
        )
        return IssuedMobilePairingChallenge(
            request_id=request_id,
            server_origin=origin,
            secret=secret,
            code=code,
            created_at=now,
            expires_at=challenge.expires_at,
        )

    def complete(
        self,
        request_id: str,
        proof: str,
        *,
        device_name: str,
        device_metadata: Mapping[str, JsonValue] | None,
        issuer: MobileCredentialIssuer,
        now: datetime,
        correlation_id: str | None = None,
    ) -> IssuedCredential:
        candidate = proof.strip()
        if not candidate:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)

        with self._lock:
            challenge = self._challenges.get(request_id)
            if challenge is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            if challenge.cancelled_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_CANCELLED)
            if challenge.consumed_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_ALREADY_USED)
            if now >= challenge.expires_at:
                raise AuthenticationError(AuthenticationFailure.PAIRING_EXPIRED)

            rate_key = f"mobile-pairing:{request_id}"
            if not self._rate_limiter.allow(rate_key, now=now):
                self._audit_failure(
                    challenge,
                    now,
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
            self._rate_limiter.record(rate_key, success=accepted, now=now)
            if not accepted:
                self._audit_failure(
                    challenge,
                    now,
                    correlation_id,
                    AuthenticationFailure.INVALID_CREDENTIALS,
                )
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)

            normalized_name, metadata = mobile_device_metadata(
                device_name,
                device_metadata,
                server_origin=challenge.server_origin,
                pairing_request_id=challenge.request_id,
            )
            issued = issuer(
                challenge.user_id,
                device_name=normalized_name,
                scope=challenge.scope,
                metadata=metadata,
                now=now,
            )
            self._challenges[request_id] = replace(challenge, consumed_at=now)

        self._audit(
            "auth.mobile_pairing_completed",
            now=now,
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

    def cancel(
        self,
        user_id: str,
        request_id: str,
        *,
        now: datetime,
        correlation_id: str | None = None,
    ) -> None:
        with self._lock:
            challenge = self._challenges.get(request_id)
            if challenge is None or challenge.user_id != user_id:
                raise KeyError(request_id)
            if challenge.consumed_at is not None:
                raise AuthenticationError(AuthenticationFailure.PAIRING_ALREADY_USED)
            if challenge.cancelled_at is None:
                self._challenges[request_id] = replace(challenge, cancelled_at=now)

        self._audit(
            "auth.mobile_pairing_cancelled",
            now=now,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={"pairing_request_id": request_id},
        )

    def _audit_failure(
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


def mobile_device_metadata(
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
        "protocol_version": MOBILE_PAIRING_PROTOCOL_VERSION,
    }
    for key, value in dict(raw_metadata or {}).items():
        if key not in _MOBILE_DEVICE_METADATA_FIELDS:
            raise ValueError(f"unsupported mobile device metadata field: {key}")
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"mobile device metadata {key} must be a non-empty short string")
        metadata[key] = value.strip()
    return normalized_name, metadata


__all__ = [
    "IssuedMobilePairingChallenge",
    "MOBILE_PAIRING_PROTOCOL_VERSION",
    "MOBILE_PAIRING_TTL",
    "MobileCredentialIssuer",
    "MobilePairingManager",
    "mobile_device_metadata",
]
