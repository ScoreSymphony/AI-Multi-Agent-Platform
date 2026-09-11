"""Safe runtime-provenance projection for application build and release evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import REDACTED, redact_sensitive, redact_text

RUNTIME_METADATA_KEYS = frozenset(
    {
        "worker_id",
        "node_id",
        "executor_id",
        "executor_version",
        "runtime_id",
        "runtime_version",
        "os",
        "architecture",
        "tool_versions",
        "build_provider_version",
        "environment_fingerprint",
    }
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def sanitize_runtime_provenance(metadata: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    """Select approved runtime fields and redact secrets or host-local filesystem paths."""

    selected = {key: metadata[key] for key in RUNTIME_METADATA_KEYS if key in metadata}
    redacted = redact_sensitive(selected)
    if not isinstance(redacted, dict):
        return {}
    return {key: _sanitize_provenance_value(value) for key, value in redacted.items()}


def _sanitize_provenance_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _sanitize_provenance_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_provenance_value(item) for item in value]
    if isinstance(value, str):
        redacted = redact_text(value)
        if _is_host_local_path(redacted):
            return REDACTED
        return redacted
    return value


def _is_host_local_path(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return (
        candidate.startswith(("/", "~/", "\\\\", "//", "file://"))
        or _WINDOWS_ABSOLUTE_PATH.match(candidate) is not None
    )
