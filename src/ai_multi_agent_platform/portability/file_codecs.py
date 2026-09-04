"""Portable File/Artifact codecs and provider-neutral file materialization for issue #79."""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.contracts import FileProvider
from ai_multi_agent_platform.data.models import DataAccessContext, FileRecord, FileState
from ai_multi_agent_platform.domain import Artifact, ExternalRef, OwnerRef, Provenance

from .dependencies import resource_dependency
from .models import DependencyRequirement, IdPolicy, PortableResource
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

FILE_PORTABLE_SCHEMA_VERSION = "1"
FILE_RESOURCE_TYPE = "file"
ARTIFACT_RESOURCE_TYPE = "artifact"


@dataclass(frozen=True, slots=True)
class FilePortableSnapshot:
    record: FileRecord
    data: bytes

    def __post_init__(self) -> None:
        if self.record.state is not FileState.READY:
            raise ValueError("only ready files can be exported portably")
        if len(self.data) != self.record.size_bytes:
            raise ValueError("portable file byte length does not match FileRecord")
        digest = hashlib.sha256(self.data).hexdigest()
        if digest != self.record.sha256:
            raise ValueError("portable file bytes do not match FileRecord checksum")


async def snapshot_file(
    provider: FileProvider,
    file_id: str,
    context: DataAccessContext,
) -> FilePortableSnapshot:
    """Read canonical metadata and bytes without exposing provider paths/object keys."""

    record = await provider.get_file(file_id, context)
    if record.state is not FileState.READY:
        raise ContractError(
            ErrorCode.CONFLICT,
            "only ready files can be exported portably",
            details={"file_id": file_id, "state": record.state.value},
        )
    chunks = [chunk async for chunk in provider.stream_file(file_id, context)]
    data = b"".join(chunks)
    if not await provider.verify_checksum(file_id, context):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "source FileProvider checksum verification failed before export",
            details={"file_id": file_id},
        )
    try:
        return FilePortableSnapshot(record=record, data=data)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "source file metadata and bytes are inconsistent",
            details={"file_id": file_id},
        ) from exc


class FilePortableCodec:
    resource_type = FILE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, FilePortableSnapshot):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "File portable codec requires a FilePortableSnapshot",
            )
        snapshot = value
        try:
            FilePortableSnapshot(record=snapshot.record, data=snapshot.data)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable file metadata and bytes are inconsistent",
                details={"file_id": snapshot.record.file_id},
            ) from exc
        return ResourceExport(
            resource_id=snapshot.record.file_id,
            resource_version="1",
            payload={
                "schema_version": FILE_PORTABLE_SCHEMA_VERSION,
                "record": _file_record_to_json(snapshot.record),
                "data_base64": base64.b64encode(snapshot.data).decode("ascii"),
            },
            id_policy=self.id_policy,
            dependencies=_file_dependencies(snapshot.record),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"File codec cannot deserialize resource type {resource.resource_type!r}",
            )
        _require_schema(resource.payload)
        raw = resource.payload.get("data_base64")
        if not isinstance(raw, str):
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "portable file data must be base64")
        try:
            data = base64.b64decode(raw, validate=True)
            record = _file_record_from_json(resource.payload.get("record"))
            record = replace(
                record,
                file_id=context.remap(FILE_RESOURCE_TYPE, record.file_id),
                project_id=_remap_optional(context, "project", record.project_id),
                artifact_ids=tuple(
                    context.remap(ARTIFACT_RESOURCE_TYPE, artifact_id)
                    for artifact_id in record.artifact_ids
                ),
            )
            return FilePortableSnapshot(record=record, data=data)
        except ContractError:
            raise
        except (binascii.Error, KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable file payload",
                details={"resource_id": resource.resource_id},
            ) from exc


class ArtifactPortableCodec:
    resource_type = ARTIFACT_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        if not isinstance(value, Artifact):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Artifact portable codec requires a canonical Artifact",
            )
        return ResourceExport(
            resource_id=value.id,
            resource_version=value.version or value.schema_version,
            payload={
                "schema_version": FILE_PORTABLE_SCHEMA_VERSION,
                "artifact": _artifact_to_json(value),
                "source_uri_omitted": value.uri is not None,
            },
            id_policy=self.id_policy,
            dependencies=_artifact_dependencies(value),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Artifact codec cannot deserialize resource type {resource.resource_type!r}",
            )
        _require_schema(resource.payload)
        try:
            artifact = _artifact_from_json(resource.payload.get("artifact"))
            return replace(
                artifact,
                id=context.remap(ARTIFACT_RESOURCE_TYPE, artifact.id),
                project_id=_remap_optional(context, "project", artifact.project_id),
                uri=None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable Artifact payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_file_portability_codecs(
    registry: ResourceSerializerRegistry,
    *,
    file_id_policy: IdPolicy = IdPolicy.PRESERVE,
    artifact_id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(FilePortableCodec(id_policy=file_id_policy))
    registry.register(ArtifactPortableCodec(id_policy=artifact_id_policy))


async def materialize_file(
    snapshot: FilePortableSnapshot,
    provider: FileProvider,
    context: DataAccessContext,
) -> FileRecord:
    """Create one imported file through the destination provider and compensate on failure."""

    record = snapshot.record
    if record.project_id != context.project_id:
        raise ContractError(
            ErrorCode.FORBIDDEN,
            "portable file project scope does not match destination access context",
            details={"file_id": record.file_id},
        )

    created = False
    try:
        imported = await provider.create_file(
            snapshot.data,
            context,
            file_id=record.file_id,
            content_type=record.content_type,
            metadata=dict(record.metadata),
        )
        created = True
        if imported.sha256 != record.sha256 or imported.size_bytes != record.size_bytes:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "destination FileProvider changed imported file bytes",
                details={"file_id": record.file_id},
            )
        if not await provider.verify_checksum(imported.file_id, context):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "destination FileProvider checksum verification failed after import",
                details={"file_id": record.file_id},
            )
        for artifact_id in record.artifact_ids:
            imported = await provider.link_artifact(imported.file_id, artifact_id, context)
        return imported
    except Exception:
        if created:
            try:
                await provider.delete_file(record.file_id, context)
            except Exception as rollback_error:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    "portable file import failed and compensating delete also failed",
                    details={"file_id": record.file_id},
                ) from rollback_error
        raise


def _file_dependencies(record: FileRecord) -> tuple[DependencyRequirement, ...]:
    dependencies: list[DependencyRequirement] = []
    if record.project_id is not None:
        dependencies.append(
            resource_dependency("project", record.project_id, purpose="File project scope")
        )
    dependencies.extend(
        resource_dependency(
            ARTIFACT_RESOURCE_TYPE,
            artifact_id,
            purpose="File artifact linkage",
        )
        for artifact_id in record.artifact_ids
    )
    return tuple(dependencies)


def _artifact_dependencies(artifact: Artifact) -> tuple[DependencyRequirement, ...]:
    if artifact.project_id is None:
        return ()
    return (
        resource_dependency("project", artifact.project_id, purpose="Artifact project scope"),
    )


def _require_schema(payload: dict[str, JsonValue]) -> None:
    if payload.get("schema_version") != FILE_PORTABLE_SCHEMA_VERSION:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "unsupported portable File/Artifact schema version",
            details={"supported_schema_version": FILE_PORTABLE_SCHEMA_VERSION},
        )


def _file_record_to_json(record: FileRecord) -> dict[str, JsonValue]:
    return {
        "file_id": record.file_id,
        "project_id": record.project_id,
        "owner_ref": record.owner_ref,
        "created_by": record.created_by,
        "created_at": record.created_at.isoformat(),
        "size_bytes": record.size_bytes,
        "sha256": record.sha256,
        "state": record.state.value,
        "content_type": record.content_type,
        "artifact_ids": list(record.artifact_ids),
        "metadata": dict(record.metadata),
    }


def _file_record_from_json(value: JsonValue | None) -> FileRecord:
    data = _object(value, "FileRecord")
    artifact_ids = data.get("artifact_ids")
    if not isinstance(artifact_ids, list) or any(not isinstance(item, str) for item in artifact_ids):
        raise ValueError("artifact_ids must be a list of strings")
    metadata = _object(data.get("metadata"), "FileRecord.metadata")
    return FileRecord(
        file_id=_string(data, "file_id"),
        project_id=_optional_string(data, "project_id"),
        owner_ref=_string(data, "owner_ref"),
        created_by=_string(data, "created_by"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        size_bytes=_integer(data, "size_bytes"),
        sha256=_string(data, "sha256"),
        state=FileState(_string(data, "state")),
        content_type=_optional_string(data, "content_type"),
        artifact_ids=tuple(cast(str, item) for item in artifact_ids),
        metadata=metadata,
    )


def _artifact_to_json(artifact: Artifact) -> dict[str, JsonValue]:
    return {
        "id": artifact.id,
        "name": artifact.name,
        "owner_ref": {"type": artifact.owner_ref.type, "id": artifact.owner_ref.id},
        "media_type": artifact.media_type,
        "version": artifact.version,
        "project_id": artifact.project_id,
        "created_at": artifact.created_at.isoformat(),
        "schema_version": artifact.schema_version,
        "provenance": _provenance_to_json(artifact.provenance),
        "external_refs": [
            {"system": item.system, "kind": item.kind, "value": item.value}
            for item in artifact.external_refs
        ],
    }


def _artifact_from_json(value: JsonValue | None) -> Artifact:
    data = _object(value, "Artifact")
    owner_data = _object(data.get("owner_ref"), "Artifact.owner_ref")
    owner_type = _string(owner_data, "type")
    if owner_type not in {"user", "organization", "team", "service"}:
        raise ValueError("Artifact.owner_ref.type is invalid")
    external_raw = data.get("external_refs")
    if not isinstance(external_raw, list):
        raise ValueError("Artifact.external_refs must be an array")
    external_refs = tuple(_external_ref(item) for item in external_raw)
    return Artifact(
        id=_string(data, "id"),
        name=_string(data, "name"),
        owner_ref=OwnerRef(
            type=cast(Literal["user", "organization", "team", "service"], owner_type),
            id=_string(owner_data, "id"),
        ),
        media_type=_string(data, "media_type"),
        uri=None,
        version=_optional_string(data, "version"),
        project_id=_optional_string(data, "project_id"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        schema_version=_string(data, "schema_version"),
        provenance=_provenance_from_json(data.get("provenance")),
        external_refs=external_refs,
    )


def _external_ref(value: JsonValue) -> ExternalRef:
    data = _object(value, "ExternalRef")
    return ExternalRef(
        system=_string(data, "system"),
        kind=_string(data, "kind"),
        value=_string(data, "value"),
    )


def _provenance_to_json(value: Provenance | None) -> JsonValue:
    if value is None:
        return None
    details: dict[str, JsonValue] = {}
    for key, item in value.details.items():
        if not isinstance(key, str) or not _is_json_value(item):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "Artifact provenance contains non-JSON metadata",
            )
        details[key] = cast(JsonValue, item)
    return {"source": value.source, "actor_ref": value.actor_ref, "details": details}


def _provenance_from_json(value: JsonValue | None) -> Provenance | None:
    if value is None:
        return None
    data = _object(value, "Provenance")
    return Provenance(
        source=_string(data, "source"),
        actor_ref=_optional_string(data, "actor_ref"),
        details=_object(data.get("details"), "Provenance.details"),
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


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _integer(data: dict[str, JsonValue], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed


def _remap_optional(context: ImportContext, resource_type: str, value: str | None) -> str | None:
    return None if value is None else context.remap(resource_type, value)
