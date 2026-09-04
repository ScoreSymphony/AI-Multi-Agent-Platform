"""Scoped-memory portability contracts and codecs for issue #79."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.contracts import MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    MemoryEntry,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
)

from .dependencies import resource_dependency
from .models import DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

MEMORY_PORTABLE_SCHEMA_VERSION = "1"
MEMORY_RESOURCE_TYPE = "memory"

_PORTABLE_MEMORY_SCOPES = frozenset(
    {
        MemoryScope.TASK,
        MemoryScope.AGENT,
        MemoryScope.WORKSPACE,
        MemoryScope.USER,
        MemoryScope.HISTORICAL,
    }
)
_PROJECT_BOUND_MEMORY_SCOPES = frozenset(
    {
        MemoryScope.TASK,
        MemoryScope.AGENT,
        MemoryScope.WORKSPACE,
    }
)


@dataclass(frozen=True, slots=True)
class MemoryPortableSnapshot:
    """Canonical durable memory plus export-time project privacy context."""

    entry: MemoryEntry
    source_project_id: str | None = None

    def __post_init__(self) -> None:
        if self.entry.scope not in _PORTABLE_MEMORY_SCOPES:
            raise ValueError(f"memory scope is not portable: {self.entry.scope.value}")
        if self.entry.expired:
            raise ValueError("expired memory is not portable")
        if self.entry.scope in _PROJECT_BOUND_MEMORY_SCOPES and self.source_project_id is None:
            raise ValueError(
                f"{self.entry.scope.value} memory requires source project context for portability"
            )
        if (
            self.entry.scope is MemoryScope.WORKSPACE
            and self.source_project_id != self.entry.scope_id
        ):
            raise ValueError("workspace memory source project must equal its canonical scope ID")


async def snapshot_memory(
    provider: MemoryProvider,
    memory_id: str,
    context: DataAccessContext,
) -> MemoryPortableSnapshot:
    """Read one memory through the authorized provider boundary and make it portable."""

    entry = await provider.get_entry(memory_id, context)
    if entry.scope is MemoryScope.SHORT_TERM:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "short-term execution memory is runtime state and cannot be exported portably",
            details={"memory_id": memory_id, "scope": entry.scope.value},
        )
    source_project_id = context.project_id if entry.scope in _PROJECT_BOUND_MEMORY_SCOPES else None
    try:
        return MemoryPortableSnapshot(entry=entry, source_project_id=source_project_id)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "memory cannot be represented by the portable privacy contract",
            details={"memory_id": memory_id, "scope": entry.scope.value},
        ) from exc


class MemoryPortableCodec:
    resource_type = MEMORY_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, MemoryPortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Memory portable codec requires a MemoryPortableSnapshot",
            )
        try:
            MemoryPortableSnapshot(
                entry=value.entry,
                source_project_id=value.source_project_id,
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "memory snapshot violates portable scope/privacy rules",
                details={"memory_id": value.entry.memory_id},
            ) from exc
        return ResourceExport(
            resource_id=value.entry.memory_id,
            resource_version=MEMORY_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": MEMORY_PORTABLE_SCHEMA_VERSION,
                "entry": _memory_entry_to_json(value.entry),
                "source_project_id": value.source_project_id,
            },
            id_policy=self.id_policy,
            dependencies=_memory_dependencies(value),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Memory codec cannot deserialize resource type {resource.resource_type!r}",
            )
        if resource.payload.get("schema_version") != MEMORY_PORTABLE_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "unsupported portable Memory schema version",
                details={"supported_schema_version": MEMORY_PORTABLE_SCHEMA_VERSION},
            )
        try:
            entry = _memory_entry_from_json(resource.payload.get("entry"))
            source_project_id = _optional_json_string(
                resource.payload.get("source_project_id"),
                "source_project_id",
            )
            remapped_project_id = (
                None if source_project_id is None else context.remap("project", source_project_id)
            )
            entry = replace(
                entry,
                memory_id=context.remap(MEMORY_RESOURCE_TYPE, entry.memory_id),
                scope_id=_remap_scope_id(context, entry.scope, entry.scope_id),
                supersedes_memory_id=_remap_optional_memory_id(context, entry.supersedes_memory_id),
                superseded_by_memory_id=_remap_optional_memory_id(
                    context, entry.superseded_by_memory_id
                ),
            )
            return MemoryPortableSnapshot(
                entry=entry,
                source_project_id=remapped_project_id,
            )
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Memory payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_memory_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(MemoryPortableCodec(id_policy=id_policy))


def _memory_dependencies(snapshot: MemoryPortableSnapshot) -> tuple[DependencyRequirement, ...]:
    entry = snapshot.entry
    dependencies: list[DependencyRequirement] = []
    seen: set[tuple[str, str]] = set()

    def append(resource_type: str, resource_id: str, purpose: str) -> None:
        key = (resource_type, resource_id)
        if key in seen:
            return
        seen.add(key)
        dependencies.append(resource_dependency(resource_type, resource_id, purpose=purpose))

    if snapshot.source_project_id is not None:
        append("project", snapshot.source_project_id, "Memory source project privacy scope")
    if entry.scope is MemoryScope.TASK:
        append("task", entry.scope_id, "Task-scoped Memory owner")
    elif entry.scope is MemoryScope.AGENT:
        append("agent", entry.scope_id, "Agent-scoped Memory owner")
    elif entry.scope is MemoryScope.WORKSPACE:
        append("project", entry.scope_id, "Workspace-scoped Memory owner")
    if entry.supersedes_memory_id is not None:
        append(MEMORY_RESOURCE_TYPE, entry.supersedes_memory_id, "Superseded Memory predecessor")
    if entry.superseded_by_memory_id is not None:
        append(MEMORY_RESOURCE_TYPE, entry.superseded_by_memory_id, "Superseding Memory successor")
    return tuple(dependencies)


def _remap_scope_id(context: ImportContext, scope: MemoryScope, scope_id: str) -> str:
    if scope is MemoryScope.TASK:
        return context.remap("task", scope_id)
    if scope is MemoryScope.AGENT:
        return context.remap("agent", scope_id)
    if scope is MemoryScope.WORKSPACE:
        return context.remap("project", scope_id)
    return scope_id


def _remap_optional_memory_id(context: ImportContext, memory_id: str | None) -> str | None:
    if memory_id is None:
        return None
    return context.remap(MEMORY_RESOURCE_TYPE, memory_id)


def _memory_entry_to_json(entry: MemoryEntry) -> dict[str, JsonValue]:
    return {
        "memory_id": entry.memory_id,
        "scope": entry.scope.value,
        "scope_id": entry.scope_id,
        "owner_ref": entry.owner_ref,
        "created_by": entry.created_by,
        "value": entry.value,
        "created_at": entry.created_at.isoformat(),
        "retention": entry.retention.value,
        "expires_at": None if entry.expires_at is None else entry.expires_at.isoformat(),
        "provenance": [_source_ref_to_json(item) for item in entry.provenance],
        "supersedes_memory_id": entry.supersedes_memory_id,
        "superseded_by_memory_id": entry.superseded_by_memory_id,
        "classification": entry.classification,
        "metadata": dict(entry.metadata),
    }


def _memory_entry_from_json(value: JsonValue | None) -> MemoryEntry:
    data = _object(value, "MemoryEntry")
    raw_provenance = data.get("provenance")
    if not isinstance(raw_provenance, list):
        raise ValueError("MemoryEntry.provenance must be an array")
    return MemoryEntry(
        memory_id=_string(data, "memory_id"),
        scope=MemoryScope(_string(data, "scope")),
        scope_id=_string(data, "scope_id"),
        owner_ref=_string(data, "owner_ref"),
        created_by=_string(data, "created_by"),
        value=data.get("value"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        retention=RetentionPolicy(_string(data, "retention")),
        expires_at=_optional_timestamp(data.get("expires_at"), "expires_at"),
        provenance=tuple(_source_ref_from_json(item) for item in raw_provenance),
        supersedes_memory_id=_optional_json_string(
            data.get("supersedes_memory_id"), "supersedes_memory_id"
        ),
        superseded_by_memory_id=_optional_json_string(
            data.get("superseded_by_memory_id"), "superseded_by_memory_id"
        ),
        classification=_optional_json_string(data.get("classification"), "classification"),
        metadata=_object(data.get("metadata"), "MemoryEntry.metadata"),
    )


def _source_ref_to_json(value: SourceRef) -> dict[str, JsonValue]:
    return {
        "kind": value.kind,
        "ref": value.ref,
        "location": value.location,
        "revision": value.revision,
        "checksum": value.checksum,
    }


def _source_ref_from_json(value: JsonValue) -> SourceRef:
    data = _object(value, "SourceRef")
    return SourceRef(
        kind=_string(data, "kind"),
        ref=_string(data, "ref"),
        location=_optional_json_string(data.get("location"), "location"),
        revision=_optional_json_string(data.get("revision"), "revision"),
        checksum=_optional_json_string(data.get("checksum"), "checksum"),
    )


def _object(value: object, field_name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{field_name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_json_string(value: JsonValue | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string or null")
    return value


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _optional_timestamp(value: JsonValue | None, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp(value, field_name)
