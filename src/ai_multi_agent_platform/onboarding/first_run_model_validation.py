"""Validation helpers for explicit first-run model-provider configuration."""

from __future__ import annotations

import hashlib
import json
from ipaddress import ip_address
from typing import cast
from urllib.parse import urlsplit

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import ModelCapabilities, ModelLocation
from ai_multi_agent_platform.security import SecretReference

_PLAINTEXT_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "bearertoken",
        "credential",
        "password",
        "secret",
        "token",
    }
)


def reject_plaintext_credentials(value: JsonValue, *, path: str = "payload") -> None:
    """Reject recursively embedded plaintext credential-shaped fields."""

    if isinstance(value, dict):
        for key, item in value.items():
            field_path = f"{path}.{key}"
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if normalized in _PLAINTEXT_CREDENTIAL_KEYS and item is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "plaintext credentials are forbidden in onboarding configuration; use a "
                    "canonical #34 SecretReference",
                    details={"field": field_path},
                )
            reject_plaintext_credentials(item, path=field_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_plaintext_credentials(item, path=f"{path}[{index}]")


def payload_digest(payload: dict[str, JsonValue]) -> str:
    """Return the stable digest used by configure-model command replay."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def optional_integer(payload: dict[str, JsonValue], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer")
    return value


def string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a list of strings")
    return tuple(item for item in raw if isinstance(item, str))


def validated_base_url(value: str) -> str:
    """Validate safe explicit HTTP(S) endpoint syntax without embedded credentials."""

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must be an explicit http(s) endpoint",
            details={"field": "base_url"},
        )
    if parsed.username is not None or parsed.password is not None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not embed credentials",
            details={"field": "base_url"},
        )
    if parsed.query or parsed.fragment:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not contain query or fragment components",
            details={"field": "base_url"},
        )
    return value.rstrip("/")


def require_loopback_endpoint(base_url: str) -> None:
    """Require local model classification to resolve to an explicit loopback endpoint."""

    hostname = urlsplit(base_url).hostname
    if hostname is None:
        raise AssertionError("validated URL must have a hostname")
    if hostname.casefold() == "localhost":
        return
    try:
        address = ip_address(hostname)
    except ValueError as exc:
        raise _local_endpoint_error(base_url) from exc
    if not address.is_loopback:
        raise _local_endpoint_error(base_url)


def golden_path_location(value: str) -> ModelLocation:
    """Resolve local/self-hosted first-run locations while excluding remote providers."""

    try:
        location = ModelLocation(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location must be local or self_hosted for first-run model setup",
            details={"location": value},
        ) from exc
    if location is ModelLocation.REMOTE:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "remote/paid providers are not configured by the first-run golden path; select them "
            "explicitly through normal model administration instead",
            details={"location": value},
        )
    return location


def optional_secret_reference(value: JsonValue | None) -> SecretReference | None:
    """Parse canonical SecretReference metadata without accepting secret material."""

    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref must be a canonical SecretReference object",
        )
    provider = value.get("provider")
    secret_id = value.get("secret_id")
    scope = value.get("scope")
    version = value.get("version")
    metadata = value.get("metadata", {})
    for field_name, field_value in (
        ("provider", provider),
        ("secret_id", secret_id),
        ("scope", scope),
    ):
        if not isinstance(field_value, str) or not field_value.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"credential_ref.{field_name} must be a non-blank string",
            )
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.version must be a non-blank string when provided",
        )
    if not isinstance(metadata, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.metadata must be a JSON object",
        )
    return SecretReference(
        provider=cast(str, provider),
        secret_id=cast(str, secret_id),
        scope=cast(str, scope),
        version=version,
        metadata=metadata,
    )


def capabilities(payload: dict[str, JsonValue]) -> ModelCapabilities:
    """Parse canonical model capabilities from an onboarding payload."""

    raw = payload.get("capabilities")
    if raw is None:
        return ModelCapabilities(modalities=("text",))
    if not isinstance(raw, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "capabilities must be a JSON object")
    context_window = raw.get("context_window")
    if context_window is not None and (
        isinstance(context_window, bool) or not isinstance(context_window, int)
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "capabilities.context_window must be an integer",
        )
    return ModelCapabilities(
        context_window=context_window,
        tool_calling=_mapping_boolean(raw, "tool_calling"),
        structured_output=_mapping_boolean(raw, "structured_output"),
        streaming=_mapping_boolean(raw, "streaming"),
        modalities=_mapping_string_tuple(raw, "modalities", default=("text",)),
        reasoning=_mapping_string_tuple(raw, "reasoning", default=()),
    )


def _local_endpoint_error(base_url: str) -> ContractError:
    return ContractError(
        ErrorCode.INVALID_REQUEST,
        "location='local' requires a loopback endpoint; use self_hosted for an explicitly "
        "remote self-managed endpoint",
        details={"base_url": base_url},
    )


def _mapping_boolean(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"capabilities.{key} must be a boolean")
    return value


def _mapping_string_tuple(
    payload: dict[str, JsonValue],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"capabilities.{key} must be a list of strings",
        )
    return tuple(item for item in value if isinstance(item, str))
