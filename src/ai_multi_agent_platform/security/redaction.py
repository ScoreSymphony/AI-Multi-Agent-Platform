"""Reusable redaction helpers for logs, traces, events and exports."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .types import SecretReference

REDACTED = "[REDACTED]"

_DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "set_cookie",
        "token",
    }
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _is_sensitive_key(key: str, extra_sensitive_keys: frozenset[str]) -> bool:
    normalized = _normalize_key(key)
    if normalized in _DEFAULT_SENSITIVE_KEYS or normalized in extra_sensitive_keys:
        return True
    return any(
        normalized.endswith(suffix)
        for suffix in ("_password", "_secret", "_token", "_api_key", "_private_key")
    )


def redact_sensitive(
    value: JsonValue | SecretReference,
    *,
    extra_sensitive_keys: frozenset[str] = frozenset(),
) -> JsonValue:
    """Return a JSON-safe copy with sensitive mappings recursively redacted.

    ``SecretReference`` objects serialize as references only; plaintext secret
    material is intentionally not part of their type.
    """

    normalized_extra = frozenset(_normalize_key(key) for key in extra_sensitive_keys)
    return _redact(value, normalized_extra)


def _redact(
    value: JsonValue | SecretReference,
    extra_sensitive_keys: frozenset[str],
) -> JsonValue:
    if isinstance(value, SecretReference):
        return {"secret_reference": value.to_dict()}
    if isinstance(value, dict):
        redacted: dict[str, JsonValue] = {}
        for key, item in value.items():
            if _is_sensitive_key(key, extra_sensitive_keys):
                redacted[key] = REDACTED
            else:
                redacted[key] = _redact(item, extra_sensitive_keys)
        return redacted
    if isinstance(value, list):
        return [_redact(item, extra_sensitive_keys) for item in value]
    return value
