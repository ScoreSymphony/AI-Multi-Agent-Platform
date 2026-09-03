"""CLI payload helpers for canonical Task-management commands."""

from __future__ import annotations

import json

from ai_multi_agent_platform.contracts.types import JsonValue

from .profiles import ProfileError


def parse_changes(raw: str) -> dict[str, JsonValue]:
    """Parse one Task-management changes object without duplicating domain validation."""

    value = _json(raw, "--changes-json")
    if not isinstance(value, dict) or not value:
        raise ProfileError("--changes-json must be a non-empty JSON object")
    if "resource_ref" in value:
        raise ProfileError("--changes-json must not contain reserved field resource_ref")
    return value


def parse_updates(raw: str) -> list[JsonValue]:
    """Parse a bulk-update array; item semantics remain Control Plane-owned."""

    value = _json(raw, "--updates-json")
    if not isinstance(value, list) or not value:
        raise ProfileError("--updates-json must be a non-empty JSON array")
    if len(value) > 100:
        raise ProfileError("--updates-json is limited to 100 updates")
    return value


def _json(raw: str, option: str) -> JsonValue:
    try:
        value: JsonValue = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileError(f"{option} must contain valid JSON") from exc
    return value
