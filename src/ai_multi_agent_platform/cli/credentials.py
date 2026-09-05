"""Secret-bearing CLI authentication state kept outside ordinary profile configuration."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .client import HTTPTransport, RawResponse
from .profiles import ProfileError

AuthMode = Literal["session", "bearer"]
_CREDENTIAL_STORE_VERSION = 1


@dataclass(frozen=True, slots=True)
class CredentialState:
    """One active authentication method for a non-secret CLI profile."""

    mode: AuthMode
    session_cookie: str | None = None
    csrf_token: str | None = None
    expires_at: str | None = None
    bearer_token: str | None = None
    credential_id: str | None = None

    def __post_init__(self) -> None:
        if self.mode == "session":
            if not self.session_cookie or not self.csrf_token:
                raise ProfileError("session credential state requires cookie and CSRF token")
            if self.bearer_token is not None:
                raise ProfileError("session credential state cannot contain a bearer token")
        elif self.mode == "bearer":
            if not self.bearer_token:
                raise ProfileError("bearer credential state requires a token")
            if self.session_cookie is not None or self.csrf_token is not None:
                raise ProfileError("bearer credential state cannot contain session secrets")
        else:
            raise ProfileError(f"unsupported CLI authentication mode: {self.mode}")

    def to_json(self) -> dict[str, str]:
        payload: dict[str, str] = {"mode": self.mode}
        for key, value in (
            ("session_cookie", self.session_cookie),
            ("csrf_token", self.csrf_token),
            ("expires_at", self.expires_at),
            ("bearer_token", self.bearer_token),
            ("credential_id", self.credential_id),
        ):
            if value is not None:
                payload[key] = value
        return payload

    @classmethod
    def from_json(cls, payload: object) -> CredentialState:
        if not isinstance(payload, dict):
            raise ProfileError("credential state must be a JSON object")
        allowed = {
            "mode",
            "session_cookie",
            "csrf_token",
            "expires_at",
            "bearer_token",
            "credential_id",
        }
        unknown = sorted(str(key) for key in payload if key not in allowed)
        if unknown:
            raise ProfileError(f"unsupported credential-state fields: {', '.join(unknown)}")
        mode = payload.get("mode")
        if mode not in {"session", "bearer"}:
            raise ProfileError("credential state requires mode 'session' or 'bearer'")
        return cls(
            mode=mode,
            session_cookie=_optional_string(payload.get("session_cookie"), "session_cookie"),
            csrf_token=_optional_string(payload.get("csrf_token"), "csrf_token"),
            expires_at=_optional_string(payload.get("expires_at"), "expires_at"),
            bearer_token=_optional_string(payload.get("bearer_token"), "bearer_token"),
            credential_id=_optional_string(payload.get("credential_id"), "credential_id"),
        )


@dataclass(slots=True)
class CredentialStore:
    """Versioned local secret store with restrictive file permissions where supported."""

    path: Path
    profiles: dict[str, CredentialState]

    @classmethod
    def load(cls, config_path: Path) -> CredentialStore:
        path = credential_store_path(config_path)
        if not path.exists():
            return cls(path=path, profiles={})
        try:
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileError(f"cannot read CLI credential store: {exc}") from exc
        if not isinstance(decoded, dict) or decoded.get("version") != _CREDENTIAL_STORE_VERSION:
            raise ProfileError("unsupported CLI credential-store version")
        raw_profiles = decoded.get("profiles")
        if not isinstance(raw_profiles, dict):
            raise ProfileError("CLI credential store requires a profiles object")
        profiles: dict[str, CredentialState] = {}
        for name, raw_state in raw_profiles.items():
            if not isinstance(name, str) or not name.strip():
                raise ProfileError("credential profile names must be non-blank strings")
            profiles[name] = CredentialState.from_json(raw_state)
        return cls(path=path, profiles=profiles)

    def get(self, profile_name: str) -> CredentialState | None:
        return self.profiles.get(profile_name)

    def set(self, profile_name: str, state: CredentialState) -> None:
        if not profile_name.strip():
            raise ProfileError("credential profile name must not be blank")
        self.profiles[profile_name] = state
        self.save()

    def clear(self, profile_name: str) -> None:
        if self.profiles.pop(profile_name, None) is not None:
            self.save()

    def save(self) -> None:
        payload = {
            "version": _CREDENTIAL_STORE_VERSION,
            "profiles": {name: state.to_json() for name, state in sorted(self.profiles.items())},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except OSError as exc:
            raise ProfileError(f"cannot write CLI credential store: {exc}") from exc


def credential_store_path(config_path: Path) -> Path:
    explicit = os.getenv("AI_PLATFORM_CREDENTIAL_STORE")
    if explicit:
        return Path(explicit).expanduser()
    suffix = config_path.suffix or ".json"
    return config_path.with_name(f"{config_path.stem}.credentials{suffix}")


class AuthenticatedTransport:
    """Inject one resolved credential at the HTTP transport boundary."""

    def __init__(self, inner: HTTPTransport, state: CredentialState | None) -> None:
        self.inner = inner
        self.state = state

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        request_headers = dict(headers)
        environment_token = os.getenv("AI_PLATFORM_TOKEN")
        if environment_token:
            request_headers["authorization"] = f"Bearer {environment_token}"
        elif self.state is not None and self.state.mode == "bearer":
            assert self.state.bearer_token is not None
            request_headers["authorization"] = f"Bearer {self.state.bearer_token}"
        elif self.state is not None and self.state.mode == "session":
            assert self.state.session_cookie is not None
            request_headers["cookie"] = self.state.session_cookie
            if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
                assert self.state.csrf_token is not None
                request_headers["x-csrf-token"] = self.state.csrf_token
        return self.inner.request(
            method,
            url,
            headers=request_headers,
            body=body,
            timeout=timeout,
        )


class CapturingTransport:
    """Capture response headers needed for HttpOnly session-cookie rotation."""

    def __init__(self, inner: HTTPTransport) -> None:
        self.inner = inner
        self.last_response: RawResponse | None = None

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> RawResponse:
        response = self.inner.request(method, url, headers=headers, body=body, timeout=timeout)
        self.last_response = response
        return response


def session_cookie_from_response(response: RawResponse | None) -> str:
    if response is None:
        raise ProfileError("authentication response was not captured")
    set_cookie = next(
        (value for key, value in response.headers.items() if key.casefold() == "set-cookie"),
        None,
    )
    if not set_cookie:
        raise ProfileError("authentication response did not include a session cookie")
    cookie_pair = set_cookie.split(";", 1)[0].strip()
    if "=" not in cookie_pair or cookie_pair.endswith("="):
        raise ProfileError("authentication response contained an invalid session cookie")
    return cookie_pair


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} must be a non-blank string")
    return value
