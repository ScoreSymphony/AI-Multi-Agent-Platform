"""Shared parsing/provenance helpers for the Skill Control Plane extension."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .codec import revision_ref_from_json
from .models import SkillRevisionRef


def owner_ref(value: object | None, context: RequestContext) -> OwnerRef:
    if value is not None:
        parsed = provided_owner_ref(value)
        assert parsed is not None
        return parsed
    if context.actor.owner_type is None or context.actor.owner_id is None:
        raise ValueError("owner_ref is required when actor owner context is unavailable")
    return OwnerRef(type=context.actor.owner_type, id=context.actor.owner_id)


def provided_owner_ref(value: object | None) -> OwnerRef | None:
    if value is None:
        return None
    data = mapping(value, "owner_ref")
    raw_type = required_string(data, "type")
    if raw_type not in {"user", "organization", "team", "service"}:
        raise ValueError("owner_ref.type must be user, organization, team or service")
    owner_type = cast(Literal["user", "organization", "team", "service"], raw_type)
    return OwnerRef(type=owner_type, id=required_string(data, "id"))


def control_plane_provenance(context: RequestContext, operation: str) -> Provenance:
    return Provenance(
        source="control-plane",
        actor_ref=context.actor.principal_ref,
        details={
            "operation": operation,
            "request_id": context.request_id,
            "correlation_id": context.correlation_id,
        },
    )


def require_collection(resource_ref: str, expected: str) -> None:
    if resource_ref != expected:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"create command resource_ref must be {expected!r}",
        )


def mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def required(mapping_value: Mapping[str, object], key: str) -> object:
    if key not in mapping_value:
        raise ValueError(f"{key} is required")
    return mapping_value[key]


def required_string(mapping_value: Mapping[str, object], key: str) -> str:
    value = mapping_value.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def optional_string(mapping_value: Mapping[str, object], key: str) -> str | None:
    value = mapping_value.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string when provided")
    return value


def positive_int(mapping_value: Mapping[str, object], key: str) -> int:
    value = mapping_value.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def optional_positive_int(mapping_value: Mapping[str, object], key: str) -> int | None:
    value = mapping_value.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer when provided")
    return value


def boolean(mapping_value: Mapping[str, object], key: str, default: bool) -> bool:
    value = mapping_value.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def revision_refs(mapping_value: Mapping[str, object], key: str) -> tuple[SkillRevisionRef, ...]:
    raw = mapping_value.get(key, [])
    if not isinstance(raw, list | tuple):
        raise ValueError(f"{key} must be an array")
    refs: list[SkillRevisionRef] = []
    for item in raw:
        ref = revision_ref_from_json(item)
        if ref is None:
            raise ValueError(f"{key} entries must be Skill revision refs")
        refs.append(ref)
    return tuple(refs)
