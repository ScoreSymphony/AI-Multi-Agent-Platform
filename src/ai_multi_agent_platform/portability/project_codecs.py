"""Portable canonical Project snapshots for issue #79."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Project, Provenance

from .models import IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

PROJECT_PORTABLE_SCHEMA_VERSION = "1"
PROJECT_RESOURCE_TYPE = "project"

OwnerType = Literal["user", "organization", "team", "service"]
_OWNER_TYPES = frozenset({"user", "organization", "team", "service"})


class ProjectPortableCodec:
    """Serialize the complete canonical Project contract without deployment-private state."""

    resource_type = PROJECT_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, Project):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Project portable codec requires a canonical Project",
            )
        return ResourceExport(
            resource_id=value.id,
            resource_version=PROJECT_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": PROJECT_PORTABLE_SCHEMA_VERSION,
                "project": _project_to_json(value),
            },
            id_policy=self.id_policy,
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Project codec cannot deserialize resource type {resource.resource_type!r}",
            )
        if resource.payload.get("schema_version") != PROJECT_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported portable Project schema version",
                details={"supported_schema_version": PROJECT_PORTABLE_SCHEMA_VERSION},
            )
        try:
            project = _project_from_json(resource.payload.get("project"))
            if project.id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable Project payload identity disagrees with resource ID",
                )
            return replace(
                project,
                id=context.remap(PROJECT_RESOURCE_TYPE, project.id),
            )
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Project payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_project_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(ProjectPortableCodec(id_policy=id_policy))


def _project_to_json(project: Project) -> dict[str, JsonValue]:
    return {
        "id": project.id,
        "name": project.name,
        "owner_ref": {
            "type": project.owner_ref.type,
            "id": project.owner_ref.id,
        },
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "schema_version": project.schema_version,
        "provenance": _provenance_to_json(project.provenance),
        "external_refs": [
            {
                "system": item.system,
                "kind": item.kind,
                "value": item.value,
            }
            for item in project.external_refs
        ],
    }


def _project_from_json(value: JsonValue | None) -> Project:
    data = _object(value, "project")
    return Project(
        id=_string(data, "id"),
        name=_string(data, "name"),
        owner_ref=_owner_from_json(data.get("owner_ref")),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        schema_version=_string(data, "schema_version"),
        provenance=_provenance_from_json(data.get("provenance")),
        external_refs=_external_refs_from_json(data.get("external_refs")),
    )


def _provenance_to_json(provenance: Provenance | None) -> JsonValue:
    if provenance is None:
        return None
    return {
        "source": provenance.source,
        "actor_ref": provenance.actor_ref,
        "details": _json_value(provenance.details),
    }


def _provenance_from_json(value: JsonValue | None) -> Provenance | None:
    if value is None:
        return None
    data = _object(value, "provenance")
    actor_ref = data.get("actor_ref")
    if actor_ref is not None and not isinstance(actor_ref, str):
        raise ValueError("provenance.actor_ref must be a string or null")
    details = _object(data.get("details"), "provenance.details")
    return Provenance(
        source=_string(data, "source"),
        actor_ref=actor_ref,
        details=details,
    )


def _owner_from_json(value: JsonValue | None) -> OwnerRef:
    data = _object(value, "owner_ref")
    owner_type = _string(data, "type")
    if owner_type not in _OWNER_TYPES:
        raise ValueError("owner_ref.type is invalid")
    return OwnerRef(
        type=cast(OwnerType, owner_type),
        id=_string(data, "id"),
    )


def _external_refs_from_json(value: JsonValue | None) -> tuple[ExternalRef, ...]:
    if not isinstance(value, list):
        raise ValueError("external_refs must be an array")
    refs: list[ExternalRef] = []
    for index, item in enumerate(value):
        data = _object(item, f"external_refs[{index}]")
        refs.append(
            ExternalRef(
                system=_string(data, "system"),
                kind=_string(data, "kind"),
                value=_string(data, "value"),
            )
        )
    return tuple(refs)


def _object(value: JsonValue | None, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _string(data: Mapping[str, JsonValue], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-blank string")
    return value


def _timestamp(value: JsonValue | None, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | bool):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("Project provenance contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Project provenance keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported Project provenance value: {type(value).__name__}")


__all__ = [
    "PROJECT_PORTABLE_SCHEMA_VERSION",
    "PROJECT_RESOURCE_TYPE",
    "ProjectPortableCodec",
    "register_project_portability_codec",
]
