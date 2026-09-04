"""Portable canonical KnowledgeSource content/configuration for issue #79."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.models import (
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeStatus,
)

from .dependencies import resource_dependency
from .models import (
    DependencyRequirement,
    ExcludedState,
    ExclusionCategory,
    IdPolicy,
    PortableResource,
)
from .registry import ImportContext, ResourceExport, ResourceSerializerRegistry

KNOWLEDGE_PORTABLE_SCHEMA_VERSION = "1"
KNOWLEDGE_SOURCE_RESOURCE_TYPE = "knowledge_source"

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_NON_PORTABLE_SOURCE_STATUSES = frozenset({KnowledgeStatus.INDEXING, KnowledgeStatus.REMOVED})


@dataclass(frozen=True, slots=True)
class KnowledgePortableSnapshot:
    """One canonical source plus current text needed to rebuild a destination index."""

    source: KnowledgeSource
    document: KnowledgeDocument | None = None

    def __post_init__(self) -> None:
        if self.source.status in _NON_PORTABLE_SOURCE_STATUSES:
            raise ValueError(
                f"knowledge source status is not portable: {self.source.status.value}"
            )
        document = self.document
        if document is None:
            if self.source.status is KnowledgeStatus.READY:
                raise ValueError("ready knowledge source portability requires current content")
            if self.source.content_checksum is not None:
                raise ValueError("knowledge source checksum requires portable current content")
            return
        if document.source_id != self.source.source_id:
            raise ValueError("knowledge document source does not match KnowledgeSource identity")
        if document.revision != self.source.revision:
            raise ValueError("knowledge document revision does not match KnowledgeSource revision")
        calculated = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
        if calculated != document.checksum:
            raise ValueError("knowledge document content checksum is invalid")
        if self.source.content_checksum not in (None, document.checksum):
            raise ValueError("KnowledgeSource content checksum disagrees with current document")
        _validate_portable_location(document.location)


def knowledge_index_exclusion(source_id: str) -> ExcludedState:
    """Describe the deliberate omission of provider/rebuildable index identity/state."""

    return ExcludedState(
        category=ExclusionCategory.REBUILDABLE_INDEX,
        path="$.knowledge.index",
        reason="knowledge indexes are rebuilt by the destination provider from canonical content",
        resource_type=KNOWLEDGE_SOURCE_RESOURCE_TYPE,
        resource_id=source_id,
    )


class KnowledgeSourcePortableCodec:
    resource_type = KNOWLEDGE_SOURCE_RESOURCE_TYPE

    def __init__(self, *, id_policy: IdPolicy = IdPolicy.PRESERVE) -> None:
        self.id_policy = id_policy

    def serialize(self, value: object) -> ResourceExport:
        snapshot = _require_snapshot(value)
        _validate_snapshot(snapshot)
        return ResourceExport(
            resource_id=snapshot.source.source_id,
            resource_version=KNOWLEDGE_PORTABLE_SCHEMA_VERSION,
            payload={
                "schema_version": KNOWLEDGE_PORTABLE_SCHEMA_VERSION,
                "source": _source_to_json(snapshot.source),
                "current_content": _document_content_to_json(snapshot.document),
                "index": {
                    "rebuild_required": True,
                    "source_revision": snapshot.source.revision,
                },
            },
            id_policy=self.id_policy,
            dependencies=_knowledge_dependencies(snapshot.source),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        if resource.resource_type != self.resource_type:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"Knowledge codec cannot deserialize resource type {resource.resource_type!r}",
            )
        try:
            if resource.payload.get("schema_version") != KNOWLEDGE_PORTABLE_SCHEMA_VERSION:
                raise ContractError(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "unsupported portable KnowledgeSource schema version",
                    details={"supported_schema_version": KNOWLEDGE_PORTABLE_SCHEMA_VERSION},
                )
            raw_index = _object(resource.payload.get("index"), "index")
            if raw_index.get("rebuild_required") is not True:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable KnowledgeSource must require destination index rebuild",
                )
            source = _source_from_json(resource.payload.get("source"))
            if source.source_id != resource.resource_id:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "portable KnowledgeSource payload identity disagrees with resource ID",
                )
            document = _document_content_from_json(
                resource.payload.get("current_content"),
                source,
            )
            target_source_id = context.remap(KNOWLEDGE_SOURCE_RESOURCE_TYPE, source.source_id)
            target_project_id = (
                None
                if source.project_id is None
                else context.remap("project", source.project_id)
            )
            remapped_source = replace(
                source,
                source_id=target_source_id,
                project_id=target_project_id,
            )
            remapped_document = (
                None
                if document is None
                else replace(document, source_id=target_source_id)
            )
            return KnowledgePortableSnapshot(
                source=remapped_source,
                document=remapped_document,
            )
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid portable KnowledgeSource payload",
                details={"resource_id": resource.resource_id},
            ) from exc


def register_knowledge_portability_codec(
    registry: ResourceSerializerRegistry,
    *,
    id_policy: IdPolicy = IdPolicy.PRESERVE,
) -> None:
    registry.register(KnowledgeSourcePortableCodec(id_policy=id_policy))


def _require_snapshot(value: object) -> KnowledgePortableSnapshot:
    if not isinstance(value, KnowledgePortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Knowledge portable codec requires a KnowledgePortableSnapshot",
        )
    return value


def _validate_snapshot(snapshot: KnowledgePortableSnapshot) -> None:
    try:
        KnowledgePortableSnapshot(source=snapshot.source, document=snapshot.document)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "KnowledgeSource violates portable content/index rules",
            details={"source_id": snapshot.source.source_id},
        ) from exc


def _knowledge_dependencies(source: KnowledgeSource) -> tuple[DependencyRequirement, ...]:
    if source.project_id is None:
        return ()
    return (
        resource_dependency(
            "project",
            source.project_id,
            purpose="KnowledgeSource project/privacy scope",
        ),
    )


def _source_to_json(source: KnowledgeSource) -> dict[str, JsonValue]:
    return {
        "source_id": source.source_id,
        "project_id": source.project_id,
        "owner_ref": source.owner_ref,
        "created_by": source.created_by,
        "title": source.title,
        "revision": source.revision,
        "source_status": source.status.value,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
        "content_checksum": source.content_checksum,
        "metadata": dict(source.metadata),
    }


def _source_from_json(value: JsonValue | None) -> KnowledgeSource:
    data = _object(value, "KnowledgeSource")
    return KnowledgeSource(
        source_id=_string(data, "source_id"),
        project_id=_optional_string(data.get("project_id"), "project_id"),
        owner_ref=_string(data, "owner_ref"),
        created_by=_string(data, "created_by"),
        title=_string(data, "title"),
        revision=_string(data, "revision"),
        status=KnowledgeStatus(_string(data, "source_status")),
        created_at=_timestamp(data.get("created_at"), "created_at"),
        updated_at=_timestamp(data.get("updated_at"), "updated_at"),
        content_checksum=_optional_checksum(data.get("content_checksum")),
        metadata=_object(data.get("metadata"), "KnowledgeSource.metadata"),
    )


def _document_content_to_json(document: KnowledgeDocument | None) -> JsonValue:
    if document is None:
        return None
    _validate_portable_location(document.location)
    return {
        "source_document_id": document.document_id,
        "revision": document.revision,
        "content": document.content,
        "location": document.location,
        "checksum": document.checksum,
        "created_at": document.created_at.isoformat(),
    }


def _document_content_from_json(
    value: JsonValue | None,
    source: KnowledgeSource,
) -> KnowledgeDocument | None:
    if value is None:
        return None
    data = _object(value, "current_content")
    location = _string(data, "location")
    _validate_portable_location(location)
    document = KnowledgeDocument(
        document_id=_string(data, "source_document_id"),
        source_id=source.source_id,
        revision=_string(data, "revision"),
        content=_string_allow_blank(data, "content"),
        location=location,
        checksum=_string(data, "checksum"),
        created_at=_timestamp(data.get("created_at"), "created_at"),
    )
    if document.revision != source.revision:
        raise ValueError("portable knowledge content revision disagrees with source revision")
    calculated = hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    if calculated != document.checksum:
        raise ValueError("portable knowledge content checksum is invalid")
    if source.content_checksum not in (None, document.checksum):
        raise ValueError("portable KnowledgeSource checksum disagrees with content")
    return document


def _validate_portable_location(location: str) -> None:
    normalized = location.strip()
    if not normalized:
        raise ValueError("knowledge document location must not be blank")
    if normalized.casefold().startswith("file://"):
        raise ValueError("file:// knowledge document locations are provider-private")
    if normalized.startswith(("/", "\\")) or _WINDOWS_ABSOLUTE_PATH.match(normalized):
        raise ValueError("absolute filesystem knowledge document locations are not portable")


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
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _string_allow_blank(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(value: JsonValue | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string or null")
    return value


def _optional_checksum(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("content_checksum must be a SHA-256 string or null")
    int(value, 16)
    return value


def _timestamp(value: JsonValue | None, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return parsed
