"""Small CLI-only parsers for canonical Workspace create payload fragments."""

from __future__ import annotations

import json

from ai_multi_agent_platform.contracts.types import JsonValue


def parse_json_array(value: str, option: str) -> list[JsonValue]:
    """Parse one JSON-array CLI option without duplicating Workspace domain validation."""

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{option} must be valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{option} must be a JSON array")
    return parsed
