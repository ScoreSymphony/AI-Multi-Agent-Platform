"""Portable payload safety validation for issue #79."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import redact_sensitive

_RUNTIME_PRIVATE_KEYS = frozenset(
    {
        "active_trace_id",
        "backend_search_id",
        "backend_vector_id",
        "cache_key",
        "filesystem_path",
        "forge_job_id",
        "forge_job_state",
        "hermes_session_id",
        "live_session_id",
        "local_pid",
        "materialization_path",
        "object_store_path",
        "process_id",
        "provider_access_token",
        "span_id",
        "temporary_cache",
        "trace_id",
        "vector_id",
        "worker_lease",
        "worker_lease_id",
        "worker_reservation",
        "worker_reservation_id",
    }
)


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_").replace(" ", "_")


def find_runtime_private_path(value: JsonValue, path: str = "$") -> str | None:
    """Return the first backend-private runtime field path, if any."""

    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if _normalize_key(key) in _RUNTIME_PRIVATE_KEYS:
                return child_path
            found = find_runtime_private_path(item, child_path)
            if found is not None:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = find_runtime_private_path(item, f"{path}[{index}]")
            if found is not None:
                return found
    return None


def validate_portable_payload(payload: Mapping[str, JsonValue]) -> None:
    """Reject plaintext secret-bearing fields and backend-private runtime state."""

    copied = dict(payload)
    if redact_sensitive(copied) != copied:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "portable payload contains plaintext secret-bearing fields",
        )

    runtime_path = find_runtime_private_path(copied)
    if runtime_path is not None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "portable payload contains backend-private runtime state",
            details={"path": runtime_path},
        )
