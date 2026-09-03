"""Reusable redaction helpers for logs, traces, events and exports."""

from __future__ import annotations

import re

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
_ENVIRONMENT_ASSIGNMENT = re.compile(
    r"(?P<prefix>\b(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|Bearer[ \t]+[^\s;\r\n]+|[^\s;\r\n]+)",
    re.IGNORECASE,
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
    material is intentionally not part of their type. Reference metadata is
    recursively redacted as defense in depth.
    """

    normalized_extra = frozenset(_normalize_key(key) for key in extra_sensitive_keys)
    return _redact(value, normalized_extra)


def redact_text(text: str, sensitive_values: tuple[str, ...] = ()) -> str:
    """Redact known secrets and obvious sensitive environment assignments from free text."""

    redacted = text
    for value in sorted((item for item in sensitive_values if item), key=len, reverse=True):
        redacted = redacted.replace(value, REDACTED)

    def redact_assignment(match: re.Match[str]) -> str:
        if not _is_sensitive_key(match.group("key"), frozenset()):
            return match.group(0)
        return f"{match.group('prefix')}{REDACTED}"

    return _ENVIRONMENT_ASSIGNMENT.sub(redact_assignment, redacted)


def redact_exception(error: BaseException, sensitive_values: tuple[str, ...] = ()) -> str:
    """Return an exception message with known sensitive substrings removed."""

    return redact_text(str(error), sensitive_values)


def _redact(
    value: JsonValue | SecretReference,
    extra_sensitive_keys: frozenset[str],
) -> JsonValue:
    if isinstance(value, SecretReference):
        return {"secret_reference": _redact(value.to_dict(), extra_sensitive_keys)}
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
