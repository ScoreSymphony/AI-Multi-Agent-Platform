"""Northbound request validation shared by focused Control Plane services."""

from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import ActorContext, OwnerType, RequestContext


def resolve_owner(
    actor: ActorContext,
    payload: dict[str, JsonValue],
) -> tuple[OwnerType, str]:
    raw_type = optional_string(payload, "owner_type") or actor.owner_type
    owner_id = optional_string(payload, "owner_id") or actor.owner_id
    if raw_type is None or owner_id is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "owner_type and owner_id are required until authentication supplies an owner context",
            details={"fields": ["owner_type", "owner_id"]},
        )
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"unsupported owner type: {raw_type}",
            details={"field": "owner_type"},
        )
    return cast(OwnerType, raw_type), owner_id


def required_string(payload: dict[str, JsonValue], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def optional_string(payload: dict[str, JsonValue], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"{name} must be a non-blank string",
            details={"field": name},
        )
    return value


def require_key(context: RequestContext) -> str:
    if context.idempotency_key is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Idempotency-Key is required for mutating commands",
            details={"header": "Idempotency-Key"},
        )
    return context.idempotency_key
