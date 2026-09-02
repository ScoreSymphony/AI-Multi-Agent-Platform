"""Reusable defensive redaction for operational and exported surfaces."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
SENSITIVE_KEY_PARTS = (
    "secret",
    "password",
    "passwd",
    "token",
    "api_key",
    "apikey",
    "credential",
    "private_key",
    "authorization",
)


def redact_text(text: str, sensitive_values: tuple[str, ...] = ()) -> str:
    redacted = text
    for value in sorted((item for item in sensitive_values if item), key=len, reverse=True):
        redacted = redacted.replace(value, REDACTED)
    return redacted


def redact_value(value: Any, sensitive_values: tuple[str, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = REDACTED
            else:
                result[key_text] = redact_value(item, sensitive_values)
        return result
    if isinstance(value, list | tuple):
        return [redact_value(item, sensitive_values) for item in value]
    if isinstance(value, str):
        return redact_text(value, sensitive_values)
    return value


def redact_exception(error: BaseException, sensitive_values: tuple[str, ...] = ()) -> str:
    return redact_text(str(error), sensitive_values)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)
