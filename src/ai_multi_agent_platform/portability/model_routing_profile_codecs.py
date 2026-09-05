"""Portable codec for canonical model-routing profile revision histories."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance
from ai_multi_agent_platform.models.routing_profile_repository import (
    ModelRoutingProfileRepository,
)
from ai_multi_agent_platform.models.routing_profiles import (
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRevision,
    RoutingProfileFallbackPolicy,
)
from ai_multi_agent_platform.models.types import RoutingRequirements

from .dependencies import resource_dependency
from .models import DependencyKind, DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

MODEL_ROUTING_PROFILE_PORTABLE_SCHEMA_VERSION = "1"
MODEL_ROUTING_PROFILE_RESOURCE_TYPE = "model_routing_profile"


@dataclass(frozen=True, slots=True)
class ModelRoutingProfilePortableSnapshot:
    """Complete immutable history plus stable lifecycle state for one routing profile."""

    definition: ModelRoutingProfileDefinition
    revisions: tuple[ModelRoutingProfileRevision, ...]

    def __post_init__(self) -> None:
        if not self.revisions:
            raise ValueError("portable routing profile snapshot requires revision history")
        if any(item.profile_id != self.definition.profile_id for item in self.revisions):
            raise ValueError("portable routing profile revisions must match the definition")
        numbers = tuple(item.revision for item in self.revisions)
        if numbers != tuple(range(1, self.definition.current_revision + 1)):
            raise ValueError("portable routing profile history must be contiguous from revision 1")
        if self.revisions[-1].revision != self.definition.current_revision:
            raise ValueError("routing profile definition must point at its latest revision")
        for revision in self.revisions:
            if (
                revision.owner_ref != self.definition.owner_ref
                or revision.project_id != self.definition.project_id
            ):
                raise ValueError("portable routing profile ownership scope is inconsistent")


def snapshot_model_routing_profile(
    repository: ModelRoutingProfileRepository,
    profile_id: str,
) -> ModelRoutingProfilePortableSnapshot:
    return ModelRoutingProfilePortableSnapshot(
        definition=repository.get_definition(profile_id),
        revisions=repository.list_revisions(profile_id),
    )


class ModelRoutingProfilePortableCodec:
    """Serialize provider-neutral routing policy without provider-private runtime state."""

    resource_type = MODEL_ROUTING_PROFILE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, ModelRoutingProfilePortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "routing profile portable codec requires a ModelRoutingProfilePortableSnapshot",
            )
        try:
            snapshot = ModelRoutingProfilePortableSnapshot(value.definition, value.revisions)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical routing profile history is not portable",
                details={"profile_id": value.definition.profile_id},
            ) from exc
        return ResourceExport(
            resource_id=snapshot.definition.profile_id,
            resource_version=str(snapshot.definition.current_revision),
            payload={
                "schema_version": MODEL_ROUTING_PROFILE_PORTABLE_SCHEMA_VERSION,
                "definition": _definition_to_json(snapshot.definition),
                "revisions": [_revision_to_json(item) for item in snapshot.revisions],
            },
            id_policy=self.id_policy,
            dependencies=_dependencies(snapshot),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"routing profile codec cannot deserialize {resource.resource_type!r}",
            )
        try:
            if resource.payload.get("schema_version") != MODEL_ROUTING_PROFILE_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable routing profile schema version",
                    details={
                        "supported_schema_version": MODEL_ROUTING_PROFILE_PORTABLE_SCHEMA_VERSION
                    },
                )
            definition = _definition_from_json(resource.payload.get("definition"))
            revisions = tuple(
                _revision_from_json(item)
                for item in _array(resource.payload.get("revisions"), "routing profile revisions")
            )
            snapshot = ModelRoutingProfilePortableSnapshot(definition, revisions)
            if definition.profile_id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable routing profile payload identity disagrees with resource ID",
                )
            if str(definition.current_revision) != resource.resource_version:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable routing profile resource version disagrees with current revision",
                )
            return _remap_snapshot(snapshot, context)
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable routing profile payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_model_routing_profile_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(ModelRoutingProfilePortableCodec(id_policy=id_policy))


def _dependencies(
    snapshot: ModelRoutingProfilePortableSnapshot,
) -> tuple[DependencyRequirement, ...]:
    dependencies: set[DependencyRequirement] = set()
    if snapshot.definition.project_id is not None:
        dependencies.add(
            resource_dependency(
                "project",
                snapshot.definition.project_id,
                purpose="Routing profile project scope",
            )
        )
    for revision in snapshot.revisions:
        model_ids: list[str] = []
        explicit = revision.policy.requirements.explicit_model_id
        if explicit is not None:
            model_ids.append(explicit)
        model_ids.extend(
            item for item in revision.policy.preferred_model_ids if item not in model_ids
        )
        for model_id in model_ids:
            dependencies.add(
                DependencyRequirement(
                    kind=DependencyKind.MODEL,
                    identifier=model_id,
                    purpose="Routing profile canonical model preference",
                )
            )
    return tuple(
        sorted(
            dependencies,
            key=lambda item: (
                item.kind.value,
                item.identifier,
                item.required,
                item.version_constraint or "",
                item.purpose or "",
            ),
        )
    )


def _remap_snapshot(
    snapshot: ModelRoutingProfilePortableSnapshot,
    context: ImportContext,
) -> ModelRoutingProfilePortableSnapshot:
    target_id = context.remap(MODEL_ROUTING_PROFILE_RESOURCE_TYPE, snapshot.definition.profile_id)
    project_id = _remap_optional(context, "project", snapshot.definition.project_id)
    definition = replace(snapshot.definition, profile_id=target_id, project_id=project_id)
    revisions = tuple(
        replace(
            revision,
            profile_id=target_id,
            project_id=project_id,
            policy=_remap_policy(revision.policy, context),
        )
        for revision in snapshot.revisions
    )
    return ModelRoutingProfilePortableSnapshot(definition, revisions)


def _remap_policy(
    policy: ModelRoutingProfilePolicy,
    context: ImportContext,
) -> ModelRoutingProfilePolicy:
    requirements = policy.requirements
    if requirements.explicit_model_id is not None:
        requirements = replace(
            requirements,
            explicit_model_id=context.remap("model", requirements.explicit_model_id),
        )
    return replace(
        policy,
        requirements=requirements,
        preferred_model_ids=tuple(
            context.remap("model", item) for item in policy.preferred_model_ids
        ),
    )


def _definition_to_json(item: ModelRoutingProfileDefinition) -> dict[str, JsonValue]:
    return {
        "profile_id": item.profile_id,
        "owner_ref": _owner_to_json(item.owner_ref),
        "current_revision": item.current_revision,
        "project_id": item.project_id,
        "enabled": item.enabled,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
        "schema_version": item.schema_version,
    }


def _revision_to_json(item: ModelRoutingProfileRevision) -> dict[str, JsonValue]:
    return {
        "profile_id": item.profile_id,
        "revision": item.revision,
        "name": item.name,
        "description": item.description,
        "owner_ref": _owner_to_json(item.owner_ref),
        "project_id": item.project_id,
        "policy": {
            "requirements": _requirements_to_json(item.policy.requirements),
            "preferred_model_ids": list(item.policy.preferred_model_ids),
            "fallback": item.policy.fallback.value,
        },
        "provenance": _provenance_to_json(item.provenance),
        "created_at": item.created_at.isoformat(),
        "schema_version": item.schema_version,
    }


def _definition_from_json(value: object) -> ModelRoutingProfileDefinition:
    item = _object(value, "routing profile definition")
    return ModelRoutingProfileDefinition(
        profile_id=_string(item, "profile_id"),
        owner_ref=_owner_from_json(item.get("owner_ref")),
        current_revision=_integer(item, "current_revision"),
        project_id=_optional_string(item, "project_id"),
        enabled=_boolean(item, "enabled"),
        created_at=_timestamp(item, "created_at"),
        updated_at=_timestamp(item, "updated_at"),
        schema_version=_string(item, "schema_version"),
    )


def _revision_from_json(value: object) -> ModelRoutingProfileRevision:
    item = _object(value, "routing profile revision")
    policy = _object(item.get("policy"), "routing profile policy")
    return ModelRoutingProfileRevision(
        profile_id=_string(item, "profile_id"),
        revision=_integer(item, "revision"),
        name=_string(item, "name"),
        description=_optional_string(item, "description") or "",
        owner_ref=_owner_from_json(item.get("owner_ref")),
        project_id=_optional_string(item, "project_id"),
        policy=ModelRoutingProfilePolicy(
            requirements=_requirements_from_json(policy.get("requirements")),
            preferred_model_ids=_strings(policy.get("preferred_model_ids"), "preferred_model_ids"),
            fallback=RoutingProfileFallbackPolicy(_string(policy, "fallback")),
        ),
        provenance=_provenance_from_json(item.get("provenance")),
        created_at=_timestamp(item, "created_at"),
        schema_version=_string(item, "schema_version"),
    )


def _requirements_to_json(item: RoutingRequirements) -> dict[str, JsonValue]:
    return {
        "explicit_model_id": item.explicit_model_id,
        "min_context_window": item.min_context_window,
        "tool_calling": item.tool_calling,
        "structured_output": item.structured_output,
        "streaming": item.streaming,
        "modalities": list(item.modalities),
        "reasoning": list(item.reasoning),
        "local_only": item.local_only,
        "self_hosted_only": item.self_hosted_only,
    }


def _requirements_from_json(value: object) -> RoutingRequirements:
    item = _object(value, "routing requirements")
    return RoutingRequirements(
        explicit_model_id=_optional_string(item, "explicit_model_id"),
        min_context_window=_optional_integer(item, "min_context_window"),
        tool_calling=_boolean(item, "tool_calling"),
        structured_output=_boolean(item, "structured_output"),
        streaming=_boolean(item, "streaming"),
        modalities=_strings(item.get("modalities"), "modalities"),
        reasoning=_strings(item.get("reasoning"), "reasoning"),
        local_only=_boolean(item, "local_only"),
        self_hosted_only=_boolean(item, "self_hosted_only"),
    )


def _owner_to_json(item: OwnerRef) -> dict[str, JsonValue]:
    return {"type": item.type, "id": item.id}


def _owner_from_json(value: object) -> OwnerRef:
    item = _object(value, "routing profile owner")
    owner_type = _string(item, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError(f"unsupported routing profile owner type: {owner_type!r}")
    typed = cast(Literal["user", "organization", "team", "service"], owner_type)
    return OwnerRef(type=typed, id=_string(item, "id"))


def _provenance_to_json(item: Provenance | None) -> JsonValue:
    if item is None:
        return None
    return {
        "source": item.source,
        "actor_ref": item.actor_ref,
        "details": dict(item.details),
    }


def _provenance_from_json(value: object) -> Provenance | None:
    if value is None:
        return None
    item = _object(value, "routing profile provenance")
    actor_ref = item.get("actor_ref")
    if actor_ref is not None and not isinstance(actor_ref, str):
        raise ValueError("routing profile provenance actor_ref must be a string or null")
    details = _object(item.get("details"), "routing profile provenance details")
    return Provenance(
        source=_string(item, "source"),
        actor_ref=actor_ref,
        details=cast(dict[str, JsonValue], details),
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be an array")
    return value


def _string(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _optional_string(item: Mapping[str, object], field: str) -> str | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string when present")
    return value


def _integer(item: Mapping[str, object], field: str) -> int:
    value = item.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _optional_integer(item: Mapping[str, object], field: str) -> int | None:
    value = item.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field} must be a positive integer when present")
    return value


def _boolean(item: Mapping[str, object], field: str) -> bool:
    value = item.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of strings")
    return tuple(cast(list[str], value))


def _timestamp(item: Mapping[str, object], field: str) -> datetime:
    parsed = datetime.fromisoformat(_string(item, field))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _remap_optional(context: ImportContext, resource_type: str, value: str | None) -> str | None:
    if value is None:
        return None
    return context.remap(resource_type, value)


__all__ = [
    "MODEL_ROUTING_PROFILE_PORTABLE_SCHEMA_VERSION",
    "MODEL_ROUTING_PROFILE_RESOURCE_TYPE",
    "ModelRoutingProfilePortableCodec",
    "ModelRoutingProfilePortableSnapshot",
    "register_model_routing_profile_portability_codec",
    "snapshot_model_routing_profile",
]
