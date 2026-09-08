"""Explicit JSON codecs shared by Skill persistence, Control Plane and portability."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models import RoutingRequirements

from .models import (
    SkillBundle,
    SkillBundleEntry,
    SkillCapabilityRequirement,
    SkillContent,
    SkillDefinition,
    SkillEvaluationStatus,
    SkillProfile,
    SkillRevision,
    SkillRevisionRef,
    SkillRiskLevel,
    SkillRunBinding,
    SkillSource,
    SkillTrustStatus,
)

OwnerType = Literal["user", "organization", "team", "service"]
_OWNER_TYPES = frozenset({"user", "organization", "team", "service"})


def skill_definition_to_json(value: SkillDefinition) -> dict[str, JsonValue]:
    return {
        "skill_id": value.skill_id,
        "owner_ref": owner_ref_to_json(value.owner_ref),
        "current_revision": value.current_revision,
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "created_at": value.created_at.isoformat(),
        "updated_at": value.updated_at.isoformat(),
    }


def skill_definition_from_json(value: object) -> SkillDefinition:
    data = _object(value, "Skill definition")
    try:
        return SkillDefinition(
            skill_id=_required_string(data, "skill_id"),
            owner_ref=owner_ref_from_json(data.get("owner_ref")),
            current_revision=_positive_int(data, "current_revision"),
            project_id=_optional_string(data, "project_id"),
            workspace_id=_optional_string(data, "workspace_id"),
            created_at=_datetime(data, "created_at"),
            updated_at=_datetime(data, "updated_at"),
        )
    except ValueError as exc:
        raise _invalid("invalid Skill definition", exc) from exc


def skill_revision_to_json(value: SkillRevision) -> dict[str, JsonValue]:
    return {
        "skill_id": value.skill_id,
        "revision": value.revision,
        "profile": skill_profile_to_json(value.profile),
        "owner_ref": owner_ref_to_json(value.owner_ref),
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "created_at": value.created_at.isoformat(),
        "provenance": provenance_to_json(value.provenance),
    }


def skill_revision_from_json(value: object) -> SkillRevision:
    data = _object(value, "Skill revision")
    try:
        return SkillRevision(
            skill_id=_required_string(data, "skill_id"),
            revision=_positive_int(data, "revision"),
            profile=skill_profile_from_json(data.get("profile")),
            owner_ref=owner_ref_from_json(data.get("owner_ref")),
            project_id=_optional_string(data, "project_id"),
            workspace_id=_optional_string(data, "workspace_id"),
            created_at=_datetime(data, "created_at"),
            provenance=provenance_from_json(data.get("provenance")),
        )
    except ValueError as exc:
        raise _invalid("invalid Skill revision", exc) from exc


def skill_profile_to_json(value: SkillProfile) -> dict[str, JsonValue]:
    return {
        "name": value.name,
        "description": value.description,
        "purpose_categories": list(value.purpose_categories),
        "content": {
            "content": value.content.content,
            "ref": value.content.ref,
            "version": value.content.version,
        },
        "dependencies": [revision_ref_to_json(item) for item in value.dependencies],
        "capability_requirements": [
            {
                "capability_id": item.capability_id,
                "exact_version": item.exact_version,
                "minimum_version": item.minimum_version,
                "maximum_version": item.maximum_version,
                "required_features": list(item.required_features),
            }
            for item in value.capability_requirements
        ],
        "compatible_agent_roles": list(value.compatible_agent_roles),
        "routing_requirements": routing_requirements_to_json(value.routing_requirements),
        "expected_inputs": list(value.expected_inputs),
        "expected_outputs": list(value.expected_outputs),
        "workspace_assumptions": list(value.workspace_assumptions),
        "side_effects": list(value.side_effects),
        "conflicts_with_skill_ids": list(value.conflicts_with_skill_ids),
        "risk_level": value.risk_level.value,
        "trust_status": value.trust_status.value,
        "evaluation_status": value.evaluation_status.value,
        "evaluation_metadata": dict(value.evaluation_metadata),
        "source": skill_source_to_json(value.source),
        "enabled": value.enabled,
        "deprecated": value.deprecated,
        "replacement": revision_ref_to_json(value.replacement),
        "metadata": dict(value.metadata),
    }


def skill_profile_from_json(value: object) -> SkillProfile:
    data = _object(value, "Skill profile")
    content_data = _object(data.get("content"), "Skill content")
    try:
        return SkillProfile(
            name=_required_string(data, "name"),
            description=_optional_string_allow_blank(data, "description") or "",
            purpose_categories=_string_tuple(data, "purpose_categories"),
            content=SkillContent(
                content=_optional_string(content_data, "content"),
                ref=_optional_string(content_data, "ref"),
                version=_optional_string(content_data, "version"),
            ),
            dependencies=_revision_ref_array(data.get("dependencies", []), "dependencies"),
            capability_requirements=tuple(
                _capability_requirement(item)
                for item in _array(
                    data.get("capability_requirements", []),
                    "capability_requirements",
                )
            ),
            compatible_agent_roles=_string_tuple(data, "compatible_agent_roles"),
            routing_requirements=routing_requirements_from_json(
                data.get("routing_requirements", {})
            ),
            expected_inputs=_string_tuple(data, "expected_inputs"),
            expected_outputs=_string_tuple(data, "expected_outputs"),
            workspace_assumptions=_string_tuple(data, "workspace_assumptions"),
            side_effects=_string_tuple(data, "side_effects"),
            conflicts_with_skill_ids=_string_tuple(data, "conflicts_with_skill_ids"),
            risk_level=SkillRiskLevel(_optional_string(data, "risk_level") or "low"),
            trust_status=SkillTrustStatus(_optional_string(data, "trust_status") or "adopted"),
            evaluation_status=SkillEvaluationStatus(
                _optional_string(data, "evaluation_status") or "not_evaluated"
            ),
            evaluation_metadata=_json_mapping(
                data.get("evaluation_metadata", {}),
                "Skill evaluation metadata",
            ),
            source=skill_source_from_json(data.get("source")),
            enabled=_boolean(data, "enabled", default=True),
            deprecated=_boolean(data, "deprecated", default=False),
            replacement=revision_ref_from_json(data.get("replacement")),
            metadata=_json_mapping(data.get("metadata", {}), "Skill metadata"),
        )
    except ValueError as exc:
        raise _invalid("invalid Skill profile", exc) from exc


def skill_source_to_json(value: SkillSource | None) -> JsonValue:
    if value is None:
        return None
    return {
        "source_url": value.source_url,
        "source_revision": value.source_revision,
        "license": value.license,
        "checksum": value.checksum,
        "signature": value.signature,
        "requested_capability_ids": list(value.requested_capability_ids),
        "filesystem_implications": list(value.filesystem_implications),
        "network_implications": list(value.network_implications),
        "embedded_hook_refs": list(value.embedded_hook_refs),
        "cost_implications": value.cost_implications,
    }


def skill_source_from_json(value: object) -> SkillSource | None:
    if value is None:
        return None
    data = _object(value, "Skill source")
    return SkillSource(
        source_url=_required_string(data, "source_url"),
        source_revision=_required_string(data, "source_revision"),
        license=_required_string(data, "license"),
        checksum=_optional_string(data, "checksum"),
        signature=_optional_string(data, "signature"),
        requested_capability_ids=_string_tuple(data, "requested_capability_ids"),
        filesystem_implications=_string_tuple(data, "filesystem_implications"),
        network_implications=_string_tuple(data, "network_implications"),
        embedded_hook_refs=_string_tuple(data, "embedded_hook_refs"),
        cost_implications=_optional_string(data, "cost_implications"),
    )


def skill_bundle_to_json(value: SkillBundle) -> dict[str, JsonValue]:
    return {
        "skill_bundle_id": value.skill_bundle_id,
        "digest": value.digest,
        "entries": [
            {
                "ref": revision_ref_to_json(item.ref),
                "content_digest": item.content_digest,
                "trust_status": item.trust_status.value,
                "source_revision": item.source_revision,
                "source_checksum": item.source_checksum,
            }
            for item in value.entries
        ],
        "resolver_version": value.resolver_version,
        "policy_version": value.policy_version,
        "run_id": value.run_id,
        "task_id": value.task_id,
        "agent_id": value.agent_id,
        "agent_revision": value.agent_revision,
        "step_id": value.step_id,
        "project_id": value.project_id,
        "workspace_id": value.workspace_id,
        "capability_ids": list(value.capability_ids),
        "capability_versions": dict(value.capability_versions),
        "created_at": value.created_at.isoformat(),
        "audit_metadata": dict(value.audit_metadata),
    }


def skill_bundle_from_json(value: object) -> SkillBundle:
    data = _object(value, "Skill Bundle")
    entries: list[SkillBundleEntry] = []
    for item in _array(data.get("entries", []), "Skill Bundle entries"):
        entry = _object(item, "Skill Bundle entry")
        ref = revision_ref_from_json(entry.get("ref"))
        if ref is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Skill Bundle entry requires ref",
            )
        entries.append(
            SkillBundleEntry(
                ref=ref,
                content_digest=_required_string(entry, "content_digest"),
                trust_status=SkillTrustStatus(_required_string(entry, "trust_status")),
                source_revision=_optional_string(entry, "source_revision"),
                source_checksum=_optional_string(entry, "source_checksum"),
            )
        )
    try:
        return SkillBundle(
            skill_bundle_id=_required_string(data, "skill_bundle_id"),
            digest=_required_string(data, "digest"),
            entries=tuple(entries),
            resolver_version=_required_string(data, "resolver_version"),
            policy_version=_required_string(data, "policy_version"),
            run_id=_required_string(data, "run_id"),
            task_id=_required_string(data, "task_id"),
            agent_id=_required_string(data, "agent_id"),
            agent_revision=_positive_int(data, "agent_revision"),
            step_id=_optional_string(data, "step_id"),
            project_id=_optional_string(data, "project_id"),
            workspace_id=_optional_string(data, "workspace_id"),
            capability_ids=_string_tuple(data, "capability_ids"),
            capability_versions=_string_mapping(
                data.get("capability_versions", {}),
                "capability_versions",
            ),
            created_at=_datetime(data, "created_at"),
            audit_metadata=_json_mapping(
                data.get("audit_metadata", {}),
                "Skill Bundle audit metadata",
            ),
        )
    except ValueError as exc:
        raise _invalid("invalid Skill Bundle", exc) from exc


def skill_binding_to_json(value: SkillRunBinding) -> dict[str, JsonValue]:
    return {
        "binding_id": value.binding_id,
        "run_id": value.run_id,
        "task_id": value.task_id,
        "agent_id": value.agent_id,
        "agent_revision": value.agent_revision,
        "skill_bundle_id": value.skill_bundle_id,
        "skill_bundle_hash": value.skill_bundle_hash,
        "agent_run_id": value.agent_run_id,
        "created_at": value.created_at.isoformat(),
    }


def skill_binding_from_json(value: object) -> SkillRunBinding:
    data = _object(value, "Skill Run binding")
    try:
        return SkillRunBinding(
            binding_id=_required_string(data, "binding_id"),
            run_id=_required_string(data, "run_id"),
            task_id=_required_string(data, "task_id"),
            agent_id=_required_string(data, "agent_id"),
            agent_revision=_positive_int(data, "agent_revision"),
            skill_bundle_id=_required_string(data, "skill_bundle_id"),
            skill_bundle_hash=_required_string(data, "skill_bundle_hash"),
            agent_run_id=_optional_string(data, "agent_run_id"),
            created_at=_datetime(data, "created_at"),
        )
    except ValueError as exc:
        raise _invalid("invalid Skill Run binding", exc) from exc


def revision_ref_to_json(value: SkillRevisionRef | None) -> JsonValue:
    if value is None:
        return None
    return {"skill_id": value.skill_id, "revision": value.revision}


def revision_ref_from_json(value: object) -> SkillRevisionRef | None:
    if value is None:
        return None
    data = _object(value, "Skill revision ref")
    return SkillRevisionRef(
        skill_id=_required_string(data, "skill_id"),
        revision=_positive_int(data, "revision"),
    )


def routing_requirements_to_json(value: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "explicit_model_id": value.explicit_model_id,
        "min_context_window": value.min_context_window,
        "tool_calling": value.tool_calling,
        "structured_output": value.structured_output,
        "streaming": value.streaming,
        "modalities": list(value.modalities),
        "reasoning": list(value.reasoning),
        "local_only": value.local_only,
        "self_hosted_only": value.self_hosted_only,
    }


def routing_requirements_from_json(value: object) -> RoutingRequirements:
    data = _object(value, "routing requirements")
    try:
        return RoutingRequirements(
            explicit_model_id=_optional_string(data, "explicit_model_id"),
            min_context_window=_optional_positive_int(data, "min_context_window"),
            tool_calling=_boolean(data, "tool_calling", default=False),
            structured_output=_boolean(data, "structured_output", default=False),
            streaming=_boolean(data, "streaming", default=False),
            modalities=_string_tuple(data, "modalities"),
            reasoning=_string_tuple(data, "reasoning"),
            local_only=_boolean(data, "local_only", default=False),
            self_hosted_only=_boolean(data, "self_hosted_only", default=False),
        )
    except ValueError as exc:
        raise _invalid("invalid routing requirements", exc) from exc


def owner_ref_to_json(value: OwnerRef) -> dict[str, JsonValue]:
    return {"type": value.type, "id": value.id}


def owner_ref_from_json(value: object) -> OwnerRef:
    data = _object(value, "owner_ref")
    owner_type = _required_string(data, "type")
    if owner_type not in _OWNER_TYPES:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "invalid owner_ref type")
    return OwnerRef(type=cast(OwnerType, owner_type), id=_required_string(data, "id"))


def provenance_to_json(value: Provenance | None) -> JsonValue:
    if value is None:
        return None
    return {
        "source": value.source,
        "actor_ref": value.actor_ref,
        "details": cast(JsonValue, dict(value.details)),
    }


def provenance_from_json(value: object) -> Provenance | None:
    if value is None:
        return None
    data = _object(value, "provenance")
    details = _object(data.get("details", {}), "provenance details")
    return Provenance(
        source=_required_string(data, "source"),
        actor_ref=_optional_string(data, "actor_ref"),
        details=details,
    )


def _capability_requirement(value: object) -> SkillCapabilityRequirement:
    data = _object(value, "Skill capability requirement")
    return SkillCapabilityRequirement(
        capability_id=_required_string(data, "capability_id"),
        exact_version=_optional_string(data, "exact_version"),
        minimum_version=_optional_string(data, "minimum_version"),
        maximum_version=_optional_string(data, "maximum_version"),
        required_features=_string_tuple(data, "required_features"),
    )


def _revision_ref_array(value: object, name: str) -> tuple[SkillRevisionRef, ...]:
    refs: list[SkillRevisionRef] = []
    for item in _array(value, name):
        ref = revision_ref_from_json(item)
        if ref is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"{name} cannot contain null revision refs",
            )
        refs.append(ref)
    return tuple(refs)


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, f"{name} must be an array")
    return cast(list[object], value)


def _required_string(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return item


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-blank string when provided")
    return item


def _optional_string_allow_blank(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string when provided")
    return item


def _positive_int(value: Mapping[str, object], key: str) -> int:
    item = value.get(key)
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _optional_positive_int(value: Mapping[str, object], key: str) -> int | None:
    item = value.get(key)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int) or item < 1:
        raise ValueError(f"{key} must be a positive integer when provided")
    return item


def _boolean(value: Mapping[str, object], key: str, *, default: bool) -> bool:
    item = value.get(key, default)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _string_tuple(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = value.get(key, [])
    if not isinstance(raw, list):
        raise ValueError(f"{key} must be an array of strings")
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{key} must contain non-blank strings")
        result.append(item)
    return tuple(result)


def _string_mapping(value: object, name: str) -> dict[str, str]:
    data = _object(value, name)
    result: dict[str, str] = {}
    for key, item in data.items():
        if not key.strip() or not isinstance(item, str) or not item.strip():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"{name} must map non-blank strings to non-blank strings",
            )
        result[key] = item
    return result


def _json_mapping(value: object, name: str) -> dict[str, JsonValue]:
    data = _object(value, name)
    return cast(dict[str, JsonValue], data)


def _datetime(value: Mapping[str, object], key: str) -> datetime:
    raw = _required_string(value, key)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed


def _invalid(message: str, exc: Exception) -> ContractError:
    return ContractError(
        ErrorCode.INVALID_CONFIGURATION,
        message,
        details={"reason": str(exc)},
    )
