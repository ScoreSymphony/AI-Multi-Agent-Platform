"""Defensive validation for untrusted platform-bound JSON values."""

from __future__ import annotations

import math

from ai_multi_agent_platform.contracts.types import JsonValue


class UntrustedInputError(ValueError):
    """Raised when untrusted input violates baseline structural/resource limits."""


def validate_untrusted_json(
    value: object,
    *,
    max_depth: int = 16,
    max_items: int = 10_000,
    max_string_length: int = 1_000_000,
) -> JsonValue:
    """Validate and return a JSON-compatible value using bounded recursion.

    This is a baseline guard, not a substitute for endpoint/capability schemas.
    It rejects non-JSON Python objects, non-finite numbers, excessive nesting,
    oversized strings and payloads with too many aggregate container items.
    """

    if max_depth < 0 or max_items < 0 or max_string_length < 0:
        raise ValueError("validation limits must not be negative")

    item_count = 0

    def visit(current: object, depth: int) -> JsonValue:
        nonlocal item_count
        if depth > max_depth:
            raise UntrustedInputError("input exceeds maximum nesting depth")
        if current is None or isinstance(current, bool | int | str):
            if isinstance(current, str) and len(current) > max_string_length:
                raise UntrustedInputError("input string exceeds maximum length")
            return current
        if isinstance(current, float):
            if not math.isfinite(current):
                raise UntrustedInputError("non-finite numbers are not allowed")
            return current
        if isinstance(current, list):
            item_count += len(current)
            if item_count > max_items:
                raise UntrustedInputError("input exceeds maximum item count")
            return [visit(item, depth + 1) for item in current]
        if isinstance(current, dict):
            item_count += len(current)
            if item_count > max_items:
                raise UntrustedInputError("input exceeds maximum item count")
            normalized: dict[str, JsonValue] = {}
            for key, item in current.items():
                if not isinstance(key, str):
                    raise UntrustedInputError("object keys must be strings")
                if len(key) > max_string_length:
                    raise UntrustedInputError("object key exceeds maximum length")
                normalized[key] = visit(item, depth + 1)
            return normalized
        raise UntrustedInputError(f"unsupported input type: {type(current).__name__}")

    return visit(value, 0)
