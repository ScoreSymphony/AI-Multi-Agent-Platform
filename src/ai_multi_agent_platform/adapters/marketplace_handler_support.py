"""Shared helpers for Marketplace owner adapters."""

from __future__ import annotations

import json

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.distribution.items import RegistryItem


def _requirements(item: RegistryItem, *, owner_domain: str) -> dict[str, object]:
    return {
        "owner_domain": owner_domain,
        "requested_permissions": tuple(sorted(item.requested_permissions)),
        "required_capabilities": tuple(sorted(item.required_capabilities)),
        "required_plugins": tuple(item.required_plugins),
        "required_connectors": tuple(item.required_connectors),
        "required_models": tuple(item.required_models),
    }


def _json_object(artifact: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(artifact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{label} artifact must be a UTF-8 JSON object",
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{label} artifact must be a JSON object",
        )
    return value
