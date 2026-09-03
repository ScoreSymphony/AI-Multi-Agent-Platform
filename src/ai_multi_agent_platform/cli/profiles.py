"""Non-secret CLI target profile storage."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

OwnerType = Literal["user", "organization", "team", "service"]
_CONFIG_VERSION = 1
_DEFAULT_ENDPOINT = "http://127.0.0.1:8000"


class ProfileError(ValueError):
    """Raised when a CLI profile/configuration is invalid."""


@dataclass(frozen=True, slots=True)
class CLIProfile:
    """Safe, non-secret connection metadata for one Control Plane target."""

    endpoint: str
    principal_ref: str | None = None
    owner_type: OwnerType | None = None
    owner_id: str | None = None

    def __post_init__(self) -> None:
        _validate_endpoint(self.endpoint)
        if (self.owner_type is None) != (self.owner_id is None):
            raise ProfileError("owner_type and owner_id must be configured together")

    def to_json(self) -> dict[str, str]:
        payload = {"endpoint": self.endpoint}
        if self.principal_ref is not None:
            payload["principal_ref"] = self.principal_ref
        if self.owner_type is not None:
            payload["owner_type"] = self.owner_type
        if self.owner_id is not None:
            payload["owner_id"] = self.owner_id
        return payload

    @classmethod
    def from_json(cls, payload: object) -> CLIProfile:
        if not isinstance(payload, dict):
            raise ProfileError("profile must be a JSON object")
        allowed = {"endpoint", "principal_ref", "owner_type", "owner_id"}
        unknown = sorted(str(key) for key in payload if key not in allowed)
        if unknown:
            raise ProfileError(f"unsupported profile fields: {', '.join(unknown)}")
        endpoint = payload.get("endpoint")
        if not isinstance(endpoint, str):
            raise ProfileError("profile endpoint must be a string")
        principal_ref = _optional_string(payload.get("principal_ref"), "principal_ref")
        owner_type_raw = _optional_string(payload.get("owner_type"), "owner_type")
        owner_type: OwnerType | None = None
        if owner_type_raw is not None:
            if owner_type_raw not in {"user", "organization", "team", "service"}:
                raise ProfileError(f"unsupported owner_type: {owner_type_raw}")
            owner_type = cast(OwnerType, owner_type_raw)
        owner_id = _optional_string(payload.get("owner_id"), "owner_id")
        return cls(
            endpoint=endpoint,
            principal_ref=principal_ref,
            owner_type=owner_type,
            owner_id=owner_id,
        )


@dataclass(slots=True)
class ProfileStore:
    """Versioned local storage containing only non-secret CLI settings."""

    path: Path
    profiles: dict[str, CLIProfile]
    current_profile: str

    @classmethod
    def load(cls, path: Path | None = None) -> ProfileStore:
        resolved = path or default_config_path()
        if not resolved.exists():
            return cls(
                path=resolved,
                profiles={"local": CLIProfile(endpoint=_DEFAULT_ENDPOINT)},
                current_profile="local",
            )
        try:
            decoded = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileError(f"cannot read CLI configuration: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ProfileError("CLI configuration must be a JSON object")
        allowed_top_level = {"version", "current_profile", "profiles"}
        unknown_top_level = sorted(str(key) for key in decoded if key not in allowed_top_level)
        if unknown_top_level:
            raise ProfileError(
                f"unsupported CLI configuration fields: {', '.join(unknown_top_level)}"
            )
        if decoded.get("version") != _CONFIG_VERSION:
            raise ProfileError("unsupported CLI configuration version")
        profiles_raw = decoded.get("profiles")
        if not isinstance(profiles_raw, dict) or not profiles_raw:
            raise ProfileError("CLI configuration requires at least one profile")
        profiles: dict[str, CLIProfile] = {}
        for name, profile_raw in profiles_raw.items():
            if not isinstance(name, str):
                raise ProfileError("profile names must be strings")
            _validate_profile_name(name)
            profiles[name] = CLIProfile.from_json(profile_raw)
        current = decoded.get("current_profile")
        if not isinstance(current, str) or current not in profiles:
            raise ProfileError("current_profile must name a configured profile")
        return cls(path=resolved, profiles=profiles, current_profile=current)

    def save(self) -> None:
        payload = {
            "version": _CONFIG_VERSION,
            "current_profile": self.current_profile,
            "profiles": {
                name: profile.to_json()
                for name, profile in sorted(self.profiles.items())
            },
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
        except OSError as exc:
            raise ProfileError(f"cannot write CLI configuration: {exc}") from exc

    def resolve(self, name: str | None = None) -> tuple[str, CLIProfile]:
        selected = name or os.getenv("AI_PLATFORM_PROFILE") or self.current_profile
        try:
            profile = self.profiles[selected]
        except KeyError as exc:
            raise ProfileError(f"unknown profile: {selected}") from exc
        endpoint_override = os.getenv("AI_PLATFORM_ENDPOINT")
        if endpoint_override is not None:
            profile = CLIProfile(
                endpoint=endpoint_override,
                principal_ref=profile.principal_ref,
                owner_type=profile.owner_type,
                owner_id=profile.owner_id,
            )
        return selected, profile

    def set_profile(self, name: str, profile: CLIProfile) -> None:
        _validate_profile_name(name)
        self.profiles[name] = profile

    def use(self, name: str) -> None:
        if name not in self.profiles:
            raise ProfileError(f"unknown profile: {name}")
        self.current_profile = name


def default_config_path() -> Path:
    explicit = os.getenv("AI_PLATFORM_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "ai-multi-agent-platform" / "cli.json"


def _validate_endpoint(endpoint: str) -> None:
    if not endpoint.strip():
        raise ProfileError("endpoint must not be blank")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ProfileError("endpoint must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ProfileError("endpoint URLs must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ProfileError("endpoint URLs must not contain query strings or fragments")


def _validate_profile_name(name: str) -> None:
    if not name or any(character.isspace() for character in name):
        raise ProfileError("profile name must be non-empty and contain no whitespace")


def _optional_string(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{field} must be a non-blank string")
    return value
