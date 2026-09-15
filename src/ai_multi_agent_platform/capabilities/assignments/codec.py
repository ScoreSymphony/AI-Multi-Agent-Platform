"""Strict canonical JSON codec for capability-assignment persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef

from .models import (
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentProvenance,
    CapabilityAssignmentRevision,
    CapabilityAssignmentRule,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
)


def policy_to_json(item: CapabilityAssignmentPolicy) -> dict[str, JsonValue]:
    return {
        "assignment_id": item.assignment_id,
        "owner_ref": _owner_to_json(item.owner_ref),
        "current_revision": item.current_revision,
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def revision_to_json(item: CapabilityAssignmentRevision) -> dict[str, JsonValue]:
    return {
        "assignment_id": item.assignment_id,
        "revision": item.revision,
        "owner_ref": _owner_to_json(item.owner_ref),
        "content": _content_to_json(item.content),
        "project_id": item.project_id,
        "organization_id": item.organization_id,
        "created_at": item.created_at.isoformat(),
    }


def policy_from_json(value: object) -> CapabilityAssignmentPolicy:
    item = _object(value, "capability-assignment policy")
    return CapabilityAssignmentPolicy(
        assignment_id=_required_string(item, "assignment_id"),
        owner_ref=_owner(_required(item, "owner_ref")),
        current_revision=_required_int(item, "current_revision"),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
        updated_at=_datetime(_required_string(item, "updated_at")),
    )


def revision_from_json(value: object) -> CapabilityAssignmentRevision:
    item = _object(value, "capability-assignment revision")
    return CapabilityAssignmentRevision(
        assignment_id=_required_string(item, "assignment_id"),
        revision=_required_int(item, "revision"),
        owner_ref=_owner(_required(item, "owner_ref")),
        content=_content(_required(item, "content")),
        project_id=_optional_string(item, "project_id"),
        organization_id=_optional_string(item, "organization_id"),
        created_at=_datetime(_required_string(item, "created_at")),
    )


def _content_to_json(item: CapabilityAssignmentContent) -> dict[str, JsonValue]:
    return {
        "target": {
            "subject_type": item.target.subject_type.value,
            "subject_id": item.target.subject_id,
        },
        "required": [_rule_to_json(rule) for rule in item.required],
        "allowed": [_rule_to_json(rule) for rule in item.allowed],
        "denied": [_rule_to_json(rule) for rule in item.denied],
        "provenance": {
            "source": item.provenance.source,
            "creator_ref": item.provenance.creator_ref,
        },
        "schema_version": item.schema_version,
    }


def _rule_to_json(item: CapabilityAssignmentRule) -> dict[str, JsonValue]:
    compatibility: JsonValue = None
    if item.compatibility is not None:
        compatibility = {
            "minimum_version": item.compatibility.minimum_version,
            "maximum_version": item.compatibility.maximum_version,
            "include_minimum": item.compatibility.include_minimum,
            "include_maximum": item.compatibility.include_maximum,
            "required_features": list(item.compatibility.required_features),
        }
    return {
        "capability_id": item.capability_id,
        "exact_version": item.exact_version,
        "compatibility": compatibility,
        "privileged": item.privileged,
        "approval_required": item.approval_required,
    }


def _owner_to_json(item: OwnerRef) -> dict[str, JsonValue]:
    return {"type": item.type, "id": item.id}


def _content(value: object) -> CapabilityAssignmentContent:
    item = _object(value, "capability-assignment content")
    target = _object(_required(item, "target"), "capability-assignment target")
    provenance = _object(_required(item, "provenance"), "capability-assignment provenance")
    return CapabilityAssignmentContent(
        target=CapabilityAssignmentTarget(
            subject_type=CapabilityAssignmentTargetType(_required_string(target, "subject_type")),
            subject_id=_required_string(target, "subject_id"),
        ),
        required=tuple(_rule(entry) for entry in _array(item, "required")),
        allowed=tuple(_rule(entry) for entry in _array(item, "allowed")),
        denied=tuple(_rule(entry) for entry in _array(item, "denied")),
        provenance=CapabilityAssignmentProvenance(
            source=_required_string(provenance, "source"),
            creator_ref=_required_string(provenance, "creator_ref"),
        ),
        schema_version=_required_string(item, "schema_version"),
    )


def _rule(value: object) -> CapabilityAssignmentRule:
    item = _object(value, "capability-assignment rule")
    compatibility_raw = item.get("compatibility")
    compatibility = None
    if compatibility_raw is not None:
        data = _object(compatibility_raw, "capability compatibility")
        compatibility = CapabilityCompatibilityRequest(
            minimum_version=_optional_string(data, "minimum_version"),
            maximum_version=_optional_string(data, "maximum_version"),
            include_minimum=_required_bool(data, "include_minimum"),
            include_maximum=_required_bool(data, "include_maximum"),
            required_features=tuple(
                _string(entry, "required feature") for entry in _array(data, "required_features")
            ),
        )
    return CapabilityAssignmentRule(
        capability_id=_required_string(item, "capability_id"),
        exact_version=_optional_string(item, "exact_version"),
        compatibility=compatibility,
        privileged=_required_bool(item, "privileged"),
        approval_required=_required_bool(item, "approval_required"),
    )


def _owner(value: object) -> OwnerRef:
    item = _object(value, "owner reference")
    owner_type = _required_string(item, "type")
    allowed = {"user", "organization", "team", "service"}
    if owner_type not in allowed:
        raise ValueError(f"unsupported owner type: {owner_type!r}")
    return OwnerRef(
        type=cast(Literal["user", "organization", "team", "service"], owner_type),
        id=_required_string(item, "id"),
    )


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(item: dict[str, object], key: str) -> list[object]:
    value = _required(item, key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return cast(list[object], value)


def _required(item: dict[str, object], key: str) -> object:
    if key not in item:
        raise ValueError(f"missing required field: {key}")
    return item[key]


def _string(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def _required_string(item: dict[str, object], key: str) -> str:
    result = _string(_required(item, key), key)
    if not result.strip():
        raise ValueError(f"{key} must not be blank")
    return result


def _optional_string(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    return _string(value, key)


def _required_int(item: dict[str, object], key: str) -> int:
    value = _required(item, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _required_bool(item: dict[str, object], key: str) -> bool:
    value = _required(item, key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("persisted datetime must be timezone-aware")
    return result
