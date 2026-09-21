"""Secure first-party mobile device pairing on top of canonical authentication credentials."""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import replace
from datetime import datetime, timedelta
from string import ascii_uppercase, digits
from typing import Any, cast
from urllib.parse import urlencode, urlsplit

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

from .authentication_models import (
    AuthenticationError,
    AuthenticationFailure,
    CredentialKind,
    IssuedCredential,
    MobilePairingChallenge,
    MobilePairingGrant,
    PairedMobileDevice,
)
from .authentication_store import InMemoryAuthenticationStore
from .authentication_tokens import authentication_now, secret_verifier
from .authorization import ActorType

_PAIRING_ALPHABET = "".join(ch for ch in ascii_uppercase + digits if ch not in "0O1I")
_PAIRING_PROTOCOL_VERSION = "1"
_FALLBACK_RATE_BUCKETS = 1024
_MOBILE_CREDENTIAL_SCOPE: dict[str, JsonValue] = {
    "actions": ["view", "read", "create", "modify", "execute", "approve"],
    "resource_types": [],
    "resource_ids": [],
}


class MobilePairingError(ValueError):
    """Safe pairing failure that never contains pairing or credential secrets."""


class MobilePairingService:
    """Issue and consume short-lived pairing proofs without becoming an auth authority."""

    def __init__(
        self,
        authentication: Any,
        *,
        challenge_ttl: timedelta = timedelta(minutes=5),
        max_failed_attempts: int = 5,
    ) -> None:
        if challenge_ttl <= timedelta(0):
            raise ValueError("mobile pairing challenge_ttl must be positive")
        if max_failed_attempts < 1:
            raise ValueError("mobile pairing max_failed_attempts must be positive")
        self.authentication = authentication
        self.store: InMemoryAuthenticationStore = cast(
            InMemoryAuthenticationStore,
            authentication.store,
        )
        self.challenge_ttl = challenge_ttl
        self.max_failed_attempts = max_failed_attempts
        self._fallback_rate_salt = secrets.token_bytes(32)
        self._lock = threading.Lock()

    def create_challenge(
        self,
        user_id: str,
        server_origin: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MobilePairingGrant:
        current = authentication_now(now)
        with self._lock:
            self._prune_expired_challenges(current)
        origin = normalize_pairing_origin(server_origin)
        self.authentication._require_account_active(self.authentication._user(user_id))
        pairing_id = new_id("pairing")
        code = _pairing_code()
        expires_at = current + self.challenge_ttl
        self.store.mobile_pairings[pairing_id] = MobilePairingChallenge(
            pairing_id=pairing_id,
            user_id=user_id,
            server_origin=origin,
            secret_verifier=secret_verifier(_normalize_code(code)),
            created_at=current,
            expires_at=expires_at,
            correlation_id=correlation_id,
        )
        self.authentication._audit(
            "auth.mobile_pairing_created",
            now=current,
            success=True,
            actor_id=user_id,
            subject_id=pairing_id,
            correlation_id=correlation_id,
            metadata={"server_origin": origin, "protocol_version": _PAIRING_PROTOCOL_VERSION},
        )
        query = urlencode(
            {
                "server": origin,
                "id": pairing_id,
                "code": code,
                "v": _PAIRING_PROTOCOL_VERSION,
            }
        )
        return MobilePairingGrant(
            pairing_id=pairing_id,
            server_origin=origin,
            secret=code,
            expires_at=expires_at,
            protocol_version=_PAIRING_PROTOCOL_VERSION,
            pairing_uri=f"amp-mobile://pair?{query}",
        )

    def cancel_challenge(
        self,
        user_id: str,
        pairing_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = authentication_now(now)
        with self._lock:
            challenge = self._owned_challenge(user_id, pairing_id)
            if challenge.consumed_at is not None:
                raise MobilePairingError("mobile pairing challenge is already used")
            if challenge.cancelled_at is None:
                self.store.mobile_pairings[pairing_id] = replace(
                    challenge,
                    cancelled_at=current,
                )
        self.authentication._audit(
            "auth.mobile_pairing_cancelled",
            now=current,
            success=True,
            actor_id=user_id,
            subject_id=pairing_id,
            correlation_id=correlation_id,
        )

    def consume_challenge(
        self,
        pairing_id: str | None,
        code: str,
        *,
        device_name: str,
        platform: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        protocol_version: str = _PAIRING_PROTOCOL_VERSION,
        caller_ref: str | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> tuple[PairedMobileDevice, IssuedCredential]:
        current = authentication_now(now)
        display_name, supplied, rate_key = self._prepare_consumption(
            pairing_id,
            code,
            device_name=device_name,
            protocol_version=protocol_version,
            caller_ref=caller_ref,
            now=current,
        )
        with self._lock:
            self._prune_expired_challenges(current, preserve_pairing_id=pairing_id)
            challenge = self._validate_pairing_proof(
                pairing_id,
                supplied,
                rate_key=rate_key,
                now=current,
                correlation_id=correlation_id,
            )
            device, issued = self._issue_mobile_device(
                challenge,
                display_name=display_name,
                platform=platform,
                metadata=metadata,
                rate_key=rate_key,
                now=current,
            )

        self.authentication._audit(
            "auth.mobile_pairing_consumed",
            now=current,
            success=True,
            actor_id=challenge.user_id,
            subject_id=device.device_id,
            credential_id=issued.credential_id,
            correlation_id=correlation_id or challenge.correlation_id,
            metadata={"platform": device.platform or "unknown"},
        )
        return device, issued

    def _prepare_consumption(
        self,
        pairing_id: str | None,
        code: str,
        *,
        device_name: str,
        protocol_version: str,
        caller_ref: str | None,
        now: datetime,
    ) -> tuple[str, str, str]:
        if protocol_version != _PAIRING_PROTOCOL_VERSION:
            raise MobilePairingError("unsupported mobile pairing protocol version")
        display_name = device_name.strip()
        if not display_name:
            raise MobilePairingError("device_name must not be blank")
        supplied = _normalize_code(code)
        if not supplied:
            raise MobilePairingError("pairing code must not be blank")
        if pairing_id is not None:
            rate_key = f"mobile-pairing:id:{pairing_id}"
        else:
            peer = caller_ref.strip() if isinstance(caller_ref, str) else ""
            proof_bucket = self._fallback_rate_bucket(supplied)
            rate_key = f"mobile-pairing:fallback:{peer or 'local'}:{proof_bucket}"
        if not self.authentication.rate_limiter.allow(rate_key, now=now):
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
        return display_name, supplied, rate_key

    def _fallback_rate_bucket(self, supplied: str) -> str:
        # A process-private salt prevents callers from selecting a particular bucket.
        # Fixed bucket cardinality bounds limiter keys per observed peer while still
        # forcing varied bogus proofs to accumulate failures instead of allocating
        # one fresh limiter key per submitted code.
        digest = hmac.digest(
            self._fallback_rate_salt,
            supplied.encode("utf-8"),
            "sha256",
        )
        bucket = int.from_bytes(digest[:4], "big") % _FALLBACK_RATE_BUCKETS
        return f"{bucket:04x}"

    def _validate_pairing_proof(
        self,
        pairing_id: str | None,
        supplied: str,
        *,
        rate_key: str,
        now: datetime,
        correlation_id: str | None,
    ) -> MobilePairingChallenge:
        challenge = self._challenge_for_proof(pairing_id, supplied, now)
        if challenge is None:
            self.authentication.rate_limiter.record(rate_key, success=False, now=now)
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if challenge.cancelled_at is not None:
            raise MobilePairingError("mobile pairing challenge is cancelled")
        if challenge.consumed_at is not None:
            raise AuthenticationError(AuthenticationFailure.REPLAY_REJECTED)
        if now >= challenge.expires_at:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_EXPIRED)
        if challenge.failed_attempts >= self.max_failed_attempts:
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
        if hmac.compare_digest(secret_verifier(supplied), challenge.secret_verifier):
            return challenge

        failed = challenge.failed_attempts + 1
        self.store.mobile_pairings[challenge.pairing_id] = replace(
            challenge,
            failed_attempts=failed,
        )
        self.authentication._audit(
            "auth.mobile_pairing_consumed",
            now=now,
            success=False,
            actor_id=challenge.user_id,
            subject_id=challenge.pairing_id,
            correlation_id=correlation_id,
            metadata={"failure": "invalid_proof", "failed_attempts": failed},
        )
        self.authentication.rate_limiter.record(rate_key, success=False, now=now)
        if failed >= self.max_failed_attempts:
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)

    def _issue_mobile_device(
        self,
        challenge: MobilePairingChallenge,
        *,
        display_name: str,
        platform: str | None,
        metadata: dict[str, JsonValue] | None,
        rate_key: str,
        now: datetime,
    ) -> tuple[PairedMobileDevice, IssuedCredential]:
        self.authentication.rate_limiter.record(rate_key, success=True, now=now)
        # Consume before issuing the durable credential while holding the service lock.
        self.store.mobile_pairings[challenge.pairing_id] = replace(
            challenge,
            consumed_at=now,
        )
        issued = cast(
            IssuedCredential,
            self.authentication.create_credential(
                challenge.user_id,
                ActorType.HUMAN,
                CredentialKind.MOBILE,
                purpose=f"mobile device: {display_name}",
                now=now,
                scope=_MOBILE_CREDENTIAL_SCOPE,
            ),
        )
        device = PairedMobileDevice(
            device_id=new_id("mobile_device"),
            user_id=challenge.user_id,
            credential_id=issued.credential_id,
            display_name=display_name,
            server_origin=challenge.server_origin,
            created_at=now,
            platform=(platform.strip() if isinstance(platform, str) and platform.strip() else None),
            metadata=metadata or {},
        )
        self.store.mobile_devices[device.device_id] = device
        return device, issued

    def list_devices(self, user_id: str) -> tuple[PairedMobileDevice, ...]:
        return tuple(
            device for device in self.store.mobile_devices.values() if device.user_id == user_id
        )

    def rename_device(
        self,
        user_id: str,
        device_id: str,
        display_name: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> PairedMobileDevice:
        current = authentication_now(now)
        normalized = display_name.strip()
        if not normalized:
            raise MobilePairingError("display_name must not be blank")
        device = self._owned_device(user_id, device_id)
        renamed = replace(device, display_name=normalized)
        self.store.mobile_devices[device_id] = renamed
        self.authentication._audit(
            "auth.mobile_device_renamed",
            now=current,
            success=True,
            actor_id=user_id,
            subject_id=device_id,
            credential_id=device.credential_id,
            correlation_id=correlation_id,
        )
        return renamed

    def revoke_device(
        self,
        user_id: str,
        device_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = authentication_now(now)
        device = self._owned_device(user_id, device_id)
        self.authentication.revoke_credential(user_id, device.credential_id, now=current)
        self.authentication._audit(
            "auth.mobile_device_revoked",
            now=current,
            success=True,
            actor_id=user_id,
            subject_id=device_id,
            credential_id=device.credential_id,
            correlation_id=correlation_id,
        )

    def revoke_all_devices(
        self,
        user_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> int:
        current = authentication_now(now)
        count = 0
        for device in self.list_devices(user_id):
            credential = self.store.credentials.get(device.credential_id)
            if credential is not None and credential.revoked_at is None:
                self.authentication.revoke_credential(user_id, device.credential_id, now=current)
                count += 1
        self.authentication._audit(
            "auth.mobile_devices_revoked",
            now=current,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={"count": count},
        )
        return count

    def safe_device(
        self,
        device: PairedMobileDevice,
        *,
        now: datetime | None = None,
    ) -> dict[str, JsonValue]:
        credential = self.store.credentials.get(device.credential_id)
        current = authentication_now(now)
        active = credential is not None and credential.active(now=current)
        return {
            "id": device.device_id,
            "user_id": device.user_id,
            "credential_id": device.credential_id,
            "scope": dict(credential.scope) if credential is not None else None,
            "display_name": device.display_name,
            "server_origin": device.server_origin,
            "platform": device.platform,
            "metadata": dict(device.metadata),
            "created_at": device.created_at.isoformat(),
            "last_used_at": (
                credential.last_used_at.isoformat()
                if credential is not None and credential.last_used_at is not None
                else None
            ),
            "revoked_at": (
                credential.revoked_at.isoformat()
                if credential is not None and credential.revoked_at is not None
                else None
            ),
            "active": active,
        }

    def _challenge_for_proof(
        self,
        pairing_id: str | None,
        supplied: str,
        now: datetime,
    ) -> MobilePairingChallenge | None:
        if pairing_id is not None:
            return self.store.mobile_pairings.get(pairing_id)
        supplied_verifier = secret_verifier(supplied)
        match: MobilePairingChallenge | None = None
        for candidate in self.store.mobile_pairings.values():
            if not candidate.active(now=now):
                continue
            # Compare every active candidate so fallback lookup does not early-exit on secrets.
            equal = hmac.compare_digest(supplied_verifier, candidate.secret_verifier)
            if equal:
                if match is not None:
                    return None
                match = candidate
        return match

    def _prune_expired_challenges(
        self,
        now: datetime,
        *,
        preserve_pairing_id: str | None = None,
    ) -> None:
        expired = [
            pairing_id
            for pairing_id, challenge in self.store.mobile_pairings.items()
            if pairing_id != preserve_pairing_id and now >= challenge.expires_at
        ]
        for pairing_id in expired:
            del self.store.mobile_pairings[pairing_id]

    def _owned_challenge(self, user_id: str, pairing_id: str) -> MobilePairingChallenge:
        challenge = self.store.mobile_pairings.get(pairing_id)
        if challenge is None or challenge.user_id != user_id:
            raise KeyError(pairing_id)
        return challenge

    def _owned_device(self, user_id: str, device_id: str) -> PairedMobileDevice:
        device = self.store.mobile_devices.get(device_id)
        if device is None or device.user_id != user_id:
            raise KeyError(device_id)
        return device


def normalize_pairing_origin(value: str) -> str:
    candidate = value.strip().rstrip("/")
    if not candidate:
        raise MobilePairingError("server_origin must not be blank")
    parsed = urlsplit(candidate)
    if parsed.username or parsed.password:
        raise MobilePairingError("server_origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise MobilePairingError("server_origin must contain only scheme, host and optional port")
    if not parsed.hostname:
        raise MobilePairingError("server_origin must be an absolute origin")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise MobilePairingError("remote mobile pairing requires HTTPS")
    return f"{parsed.scheme}://{parsed.netloc}"


def _pairing_code() -> str:
    raw = "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(12))
    return "-".join(raw[index : index + 4] for index in range(0, 12, 4))


def _normalize_code(value: str) -> str:
    return value.replace("-", "").replace(" ", "").upper().strip()


__all__ = [
    "MobilePairingError",
    "MobilePairingService",
    "normalize_pairing_origin",
]
