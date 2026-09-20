"""Short-lived mobile device pairing over the canonical Authentication authority."""

from __future__ import annotations

import hmac
import secrets
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import new_id

from .authentication_audit import AuthenticationAuditEmitter
from .authentication_credentials import AuthenticationCredentialService
from .authentication_models import (
    AuthenticationError,
    AuthenticationFailure,
    CredentialKind,
    IssuedCredential,
    IssuedMobilePairing,
    LocalUserAccount,
    MobileDevice,
    MobileDeviceGrant,
    MobilePairingChallenge,
)
from .authentication_store import InMemoryAuthenticationStore
from .authentication_tokens import authentication_now, secret_verifier
from .authorization import ActorType, AuthorizationAction

PAIRING_PROTOCOL_VERSION = 1
PAIRING_URI_SCHEME = "aiagentplatform"
_PAIRING_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_MOBILE_ACTION_SCOPE = {
    AuthorizationAction.VIEW,
    AuthorizationAction.READ,
    AuthorizationAction.CREATE,
    AuthorizationAction.MODIFY,
    AuthorizationAction.EXECUTE,
    AuthorizationAction.APPROVE,
    AuthorizationAction.RESULT_READ,
}


class AuthenticationMobilePairingService:
    """Own pairing challenges and device metadata while reusing bearer credentials."""

    def __init__(
        self,
        *,
        store: InMemoryAuthenticationStore,
        credentials: AuthenticationCredentialService,
        user: Callable[[str], LocalUserAccount],
        require_account_active: Callable[[LocalUserAccount], None],
        audit: AuthenticationAuditEmitter,
        pairing_ttl: timedelta = timedelta(minutes=5),
        max_failed_attempts: int = 5,
    ) -> None:
        if pairing_ttl <= timedelta(0):
            raise ValueError("mobile pairing TTL must be positive")
        if max_failed_attempts < 1:
            raise ValueError("mobile pairing failed-attempt limit must be positive")
        self.store = store
        self.credentials = credentials
        self.user = user
        self.require_account_active = require_account_active
        self.audit = audit
        self.pairing_ttl = pairing_ttl
        self.max_failed_attempts = max_failed_attempts
        self._mutation_lock = threading.RLock()

    def create_pairing(
        self,
        user_id: str,
        server_origin: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> IssuedMobilePairing:
        current = authentication_now(now)
        self.require_account_active(self.user(user_id))
        origin = normalize_pairing_server_origin(server_origin)
        with self._mutation_lock:
            pairing_id = new_id("mobile_pairing")
            locator = self._new_locator()
            proof = _random_code(20)
            self.store.mobile_pairings[pairing_id] = MobilePairingChallenge(
                pairing_id=pairing_id,
                user_id=user_id,
                server_origin=origin,
                code_locator=locator,
                secret_verifier=secret_verifier(proof),
                protocol_version=PAIRING_PROTOCOL_VERSION,
                created_at=current,
                expires_at=current + self.pairing_ttl,
            )
        code = f"{locator}-{proof}"
        qr_payload = (
            f"{PAIRING_URI_SCHEME}://pair?"
            + urlencode(
                {
                    "v": str(PAIRING_PROTOCOL_VERSION),
                    "origin": origin,
                    "pairing_id": pairing_id,
                    "code": code,
                }
            )
        )
        self.audit(
            "auth.mobile_pairing_created",
            now=current,
            success=True,
            actor_id=user_id,
            subject_id=pairing_id,
            correlation_id=correlation_id,
            metadata={"server_origin": origin, "protocol_version": PAIRING_PROTOCOL_VERSION},
        )
        return IssuedMobilePairing(
            pairing_id=pairing_id,
            pairing_code=code,
            server_origin=origin,
            protocol_version=PAIRING_PROTOCOL_VERSION,
            created_at=current,
            expires_at=current + self.pairing_ttl,
            qr_payload=qr_payload,
        )

    def consume_pairing(
        self,
        pairing_code: str,
        *,
        server_origin: str,
        device_name: str,
        device_platform: str,
        pairing_id: str | None = None,
        protocol_version: int = PAIRING_PROTOCOL_VERSION,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> MobileDeviceGrant:
        current = authentication_now(now)
        origin = normalize_pairing_server_origin(server_origin)
        if protocol_version != PAIRING_PROTOCOL_VERSION:
            raise ValueError("unsupported mobile pairing protocol version")
        locator, proof = _parse_pairing_code(pairing_code)
        with self._mutation_lock:
            return self._consume_pairing_locked(
                locator,
                proof,
                server_origin=origin,
                device_name=device_name,
                device_platform=device_platform,
                pairing_id=pairing_id,
                protocol_version=protocol_version,
                now=current,
                correlation_id=correlation_id,
            )

    def _consume_pairing_locked(
        self,
        locator: str,
        proof: str,
        *,
        server_origin: str,
        device_name: str,
        device_platform: str,
        pairing_id: str | None,
        protocol_version: int,
        now: datetime,
        correlation_id: str | None,
    ) -> MobileDeviceGrant:
        current = now
        origin = server_origin
        challenge = self._resolve_challenge(locator, pairing_id)
        if challenge.protocol_version != protocol_version or challenge.server_origin != origin:
            self._record_failure(challenge, current, correlation_id=correlation_id)
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        if challenge.cancelled_at is not None:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_REVOKED)
        if challenge.consumed_at is not None:
            raise AuthenticationError(AuthenticationFailure.REPLAY_REJECTED)
        if current >= challenge.expires_at:
            raise AuthenticationError(AuthenticationFailure.CREDENTIAL_EXPIRED)
        if challenge.failed_attempts >= self.max_failed_attempts:
            raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
        if not hmac.compare_digest(secret_verifier(proof), challenge.secret_verifier):
            updated = self._record_failure(challenge, current, correlation_id=correlation_id)
            if updated.failed_attempts >= self.max_failed_attempts:
                raise AuthenticationError(AuthenticationFailure.RATE_LIMITED)
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)

        self.require_account_active(self.user(challenge.user_id))
        display_name = _required_metadata(device_name, "device_name", max_length=128)
        platform = _required_metadata(device_platform, "device_platform", max_length=32).casefold()
        if platform not in {"android", "ios"}:
            raise ValueError("device_platform must be android or ios")

        device_id = new_id("mobile_device")
        scope: dict[str, JsonValue] = {
            "actions": sorted(action.value for action in _MOBILE_ACTION_SCOPE),
            "resource_types": [],
            "resource_ids": [],
        }
        credential = self.credentials.create_credential(
            challenge.user_id,
            ActorType.HUMAN,
            CredentialKind.MOBILE,
            purpose=f"mobile device {device_id}",
            now=current,
            scope=scope,
        )
        device = MobileDevice(
            device_id=device_id,
            user_id=challenge.user_id,
            credential_id=credential.credential_id,
            display_name=display_name,
            platform=platform,
            created_at=current,
        )
        self.store.mobile_devices[device_id] = device
        self.store.mobile_pairings[challenge.pairing_id] = replace(
            challenge,
            consumed_at=current,
        )
        self.audit(
            "auth.mobile_pairing_consumed",
            now=current,
            success=True,
            actor_id=challenge.user_id,
            subject_id=device_id,
            credential_id=credential.credential_id,
            correlation_id=correlation_id,
            metadata={
                "pairing_id": challenge.pairing_id,
                "server_origin": origin,
                "device_platform": platform,
            },
        )
        return MobileDeviceGrant(device=device, credential=credential)

    def cancel_pairing(
        self,
        user_id: str,
        pairing_id: str,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> None:
        current = authentication_now(now)
        challenge = self._owned_pairing(user_id, pairing_id)
        if challenge.consumed_at is not None:
            raise AuthenticationError(AuthenticationFailure.REPLAY_REJECTED)
        if challenge.cancelled_at is None:
            self.store.mobile_pairings[pairing_id] = replace(challenge, cancelled_at=current)
            self.audit(
                "auth.mobile_pairing_cancelled",
                now=current,
                success=True,
                actor_id=user_id,
                subject_id=pairing_id,
                correlation_id=correlation_id,
            )

    def list_devices(self, user_id: str) -> tuple[MobileDevice, ...]:
        return tuple(
            device for device in self.store.mobile_devices.values() if device.user_id == user_id
        )

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
        self.credentials.revoke_credential(user_id, device.credential_id, now=current)
        self.audit(
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
        revoked = 0
        for device in self.list_devices(user_id):
            credential = self.store.credentials.get(device.credential_id)
            if credential is None or credential.revoked_at is not None:
                continue
            self.credentials.revoke_credential(user_id, device.credential_id, now=current)
            revoked += 1
        self.audit(
            "auth.mobile_devices_revoked_all",
            now=current,
            success=True,
            actor_id=user_id,
            correlation_id=correlation_id,
            metadata={"revoked_count": revoked},
        )
        return revoked

    def _resolve_challenge(
        self,
        locator: str,
        pairing_id: str | None,
    ) -> MobilePairingChallenge:
        if pairing_id is not None:
            challenge = self.store.mobile_pairings.get(pairing_id)
            if challenge is None:
                raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
            return challenge
        matches = [
            item for item in self.store.mobile_pairings.values() if item.code_locator == locator
        ]
        if len(matches) != 1:
            raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
        return matches[0]

    def _record_failure(
        self,
        challenge: MobilePairingChallenge,
        now: datetime,
        *,
        correlation_id: str | None,
    ) -> MobilePairingChallenge:
        updated = replace(challenge, failed_attempts=challenge.failed_attempts + 1)
        self.store.mobile_pairings[challenge.pairing_id] = updated
        self.audit(
            "auth.mobile_pairing_failed",
            now=now,
            success=False,
            actor_id=challenge.user_id,
            subject_id=challenge.pairing_id,
            correlation_id=correlation_id,
            metadata={"failure": "invalid_pairing_proof", "attempt": updated.failed_attempts},
        )
        return updated

    def _owned_pairing(self, user_id: str, pairing_id: str) -> MobilePairingChallenge:
        challenge = self.store.mobile_pairings.get(pairing_id)
        if challenge is None or challenge.user_id != user_id:
            raise KeyError(pairing_id)
        return challenge

    def _owned_device(self, user_id: str, device_id: str) -> MobileDevice:
        device = self.store.mobile_devices.get(device_id)
        if device is None or device.user_id != user_id:
            raise KeyError(device_id)
        return device

    def _new_locator(self) -> str:
        for _ in range(32):
            locator = _random_code(8)
            if all(item.code_locator != locator for item in self.store.mobile_pairings.values()):
                return locator
        raise RuntimeError("unable to allocate unique mobile pairing locator")


def normalize_pairing_server_origin(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("server_origin must not be blank")
    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.hostname:
        raise ValueError("server_origin must be an absolute origin")
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ValueError("remote mobile pairing requires HTTPS")
    if parsed.username or parsed.password:
        raise ValueError("server_origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("server_origin must contain only scheme, host and optional port")
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_pairing_code(value: str) -> tuple[str, str]:
    locator, separator, proof = value.strip().upper().partition("-")
    if not separator or len(locator) != 8 or len(proof) != 20:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
    if any(character not in _PAIRING_ALPHABET for character in locator + proof):
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
    return locator, proof


def _random_code(length: int) -> str:
    return "".join(secrets.choice(_PAIRING_ALPHABET) for _ in range(length))


def _required_metadata(value: str, name: str, *, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{name} must contain 1..{max_length} characters")
    return normalized


__all__ = [
    "AuthenticationMobilePairingService",
    "PAIRING_PROTOCOL_VERSION",
    "PAIRING_URI_SCHEME",
    "normalize_pairing_server_origin",
]
