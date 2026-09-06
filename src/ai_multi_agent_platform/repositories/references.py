"""Serialization helpers for repository-owned canonical external-resource references."""

from __future__ import annotations

from ai_multi_agent_platform.connectors import ExternalNativeReference, ExternalResourceReference
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue


def collaboration_reference_from_json(
    value: JsonValue,
    *,
    expected_resource_type: str,
) -> ExternalResourceReference:
    """Decode an issue/change-request reference without accepting provider-private types."""

    if not isinstance(value, dict):
        raise _invalid("collaboration resource must be an object")
    data = value
    resource_type = _required_string(data, "resource_type")
    if resource_type != expected_resource_type:
        raise _invalid(f"collaboration resource must have type {expected_resource_type}")
    native = data.get("native_reference")
    if not isinstance(native, dict):
        raise _invalid("collaboration resource native_reference must be an object")
    native_data = native
    provenance = _optional_mapping(data.get("provenance"), "provenance")
    metadata = _optional_mapping(data.get("metadata"), "metadata")
    try:
        return ExternalResourceReference(
            id=_required_string(data, "id"),
            connection_id=_required_string(data, "connection_id"),
            resource_type=resource_type,
            native_reference=ExternalNativeReference(
                namespace=_required_string(native_data, "namespace"),
                native_id=_required_string(native_data, "native_id"),
            ),
            canonical_url=_optional_string(data.get("canonical_url"), "canonical_url"),
            version=_optional_string(data.get("version"), "version"),
            revision=_optional_string(data.get("revision"), "revision"),
            provenance=provenance,
            metadata=metadata,
        )
    except ValueError as exc:
        raise _invalid("collaboration resource violates the external-resource contract") from exc


def _required_string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"collaboration resource field {key} must be a non-blank string")
    return value


def _optional_string(value: JsonValue | None, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _invalid(f"collaboration resource field {key} must be a non-blank string or null")
    return value


def _optional_mapping(value: JsonValue | None, key: str) -> dict[str, JsonValue]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid(f"collaboration resource field {key} must be an object")
    return dict(value)


def _invalid(message: str) -> ContractError:
    return ContractError(ErrorCode.INVALID_REQUEST, message)
