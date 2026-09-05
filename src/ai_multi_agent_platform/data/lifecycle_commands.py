"""Canonical mutating Memory and Knowledge commands for issue #251."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane.extensions import CommandHandler
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.domain import validate_id

from .models import (
    DataAccessContext,
    KnowledgeDocument,
    KnowledgeSource,
    KnowledgeStatus,
    MemoryEntry,
    MemoryOrigin,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_knowledge_source_id,
    new_memory_id,
)
from .registry import DataProviderSet

ProjectIdProvider = Callable[[], tuple[str, ...]]

MEMORY_COMMANDS = (
    "memory.create",
    "memory.promote",
    "memory.update",
    "memory.expire",
    "memory.delete",
)
KNOWLEDGE_COMMANDS = (
    "knowledge.register",
    "knowledge.update",
    "knowledge.ingest",
    "knowledge.reindex",
    "knowledge.detach",
    "knowledge.delete",
)
DATA_CONTENT_COMMANDS = MEMORY_COMMANDS + KNOWLEDGE_COMMANDS


def data_command_handlers(
    providers: DataProviderSet,
    *,
    project_ids: ProjectIdProvider | None = None,
) -> dict[str, CommandHandler]:
    """Return idempotency-gated Control Plane commands for #251 content lifecycle."""

    known_project_ids = project_ids or (lambda: ())

    async def memory_create(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        scope = _memory_scope(_required_string(payload, "scope"))
        scope_id = _optional_string(payload, "scope_id") or resource_ref
        if scope_id != resource_ref:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory.create resource_ref must match the target scope_id",
            )
        origin = _memory_origin(_required_string(payload, "origin"))
        now = datetime.now(UTC)
        retention = _retention(payload, scope)
        expires_at = _expires_at(payload)
        if scope is MemoryScope.SHORT_TERM and expires_at is None:
            expires_at = now + timedelta(hours=1)
        project_id = _project_id_for_memory_scope(payload, scope, scope_id)
        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=scope,
            scope_id=scope_id,
            owner_ref=_memory_owner_ref(context, scope, scope_id),
            created_by=context.actor.principal_ref,
            value=_required_value(payload, "value"),
            created_at=now,
            retention=retention,
            expires_at=expires_at,
            provenance=_provenance(payload),
            classification=_optional_string(payload, "classification"),
            metadata=_object(payload.get("metadata", {}), "metadata"),
            origin=origin,
        )
        stored = await providers.memory.write_entry(
            entry,
            _data_context(context, project_id=project_id),
        )
        return _memory_resource(stored)

    async def memory_promote(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current, _ = await _get_memory(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        if current.scope is not MemoryScope.SHORT_TERM:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory.promote only accepts a canonical short-term Memory entry",
            )
        target_scope = _memory_scope(_required_string(payload, "scope"))
        if target_scope is MemoryScope.SHORT_TERM:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory.promote target scope must be durable",
            )
        target_scope_id = _required_string(payload, "scope_id")
        project_id = _project_id_for_memory_scope(payload, target_scope, target_scope_id)
        promoted = MemoryEntry(
            memory_id=new_memory_id(),
            scope=target_scope,
            scope_id=target_scope_id,
            owner_ref=_memory_owner_ref(context, target_scope, target_scope_id),
            created_by=context.actor.principal_ref,
            value=current.value,
            created_at=datetime.now(UTC),
            retention=_retention(payload, target_scope),
            expires_at=_expires_at(payload),
            provenance=(*current.provenance, SourceRef(kind="memory", ref=current.memory_id)),
            classification=current.classification,
            metadata=dict(current.metadata),
            origin=current.origin,
        )
        stored = await providers.memory.write_entry(
            promoted,
            _data_context(context, project_id=project_id),
        )
        return _memory_resource(stored)

    async def memory_update(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        current, access = await _get_memory(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        requested_origin = payload.get("origin")
        if requested_origin is not None and requested_origin != current.origin.value:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory origin is immutable; create or promote a new entry instead",
            )
        requested_scope = payload.get("scope")
        if requested_scope is not None and requested_scope != current.scope.value:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory scope is immutable during update; use memory.promote where applicable",
            )
        requested_scope_id = payload.get("scope_id")
        if requested_scope_id is not None and requested_scope_id != current.scope_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "memory scope_id is immutable during update",
            )
        replacement = MemoryEntry(
            memory_id=new_memory_id(),
            scope=current.scope,
            scope_id=current.scope_id,
            owner_ref=current.owner_ref,
            created_by=context.actor.principal_ref,
            value=payload["value"] if "value" in payload else current.value,
            created_at=datetime.now(UTC),
            retention=(
                _retention(payload, current.scope) if "retention" in payload else current.retention
            ),
            expires_at=(_expires_at(payload) if "expires_at" in payload else current.expires_at),
            provenance=(*current.provenance, SourceRef(kind="memory", ref=current.memory_id)),
            classification=(
                _optional_string(payload, "classification")
                if "classification" in payload
                else current.classification
            ),
            metadata=(
                _object(payload.get("metadata"), "metadata")
                if "metadata" in payload
                else dict(current.metadata)
            ),
            origin=current.origin,
        )
        stored = await providers.memory.supersede_entry(resource_ref, replacement, access)
        return _memory_resource(stored)

    async def memory_expire(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        scope = _memory_scope(_required_string(payload, "scope"))
        scope_id = _required_string(payload, "scope_id")
        project_id = _project_id_for_memory_scope(payload, scope, scope_id)
        access = _data_context(context, project_id=project_id)
        expired = await providers.memory.expire_entry(
            resource_ref,
            MemoryQuery(
                scope=scope,
                scope_id=scope_id,
                include_expired=True,
                include_superseded=True,
                limit=1,
            ),
            access,
        )
        return {"id": expired.memory_id, "type": "memory", "expired": True}

    async def memory_delete(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        current, access = await _get_memory(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        await providers.memory.delete_entry(current.memory_id, access)
        return {"id": current.memory_id, "type": "memory", "deleted": True}

    async def knowledge_register(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        project_id = _optional_string(payload, "project_id")
        if project_id is not None:
            validate_id(project_id, "project")
            if resource_ref != project_id:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "knowledge.register resource_ref must match project_id",
                )
        elif resource_ref != context.actor.principal_ref:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "unscoped knowledge.register must target the authenticated principal",
            )
        now = datetime.now(UTC)
        source = KnowledgeSource(
            source_id=new_knowledge_source_id(),
            project_id=project_id,
            owner_ref=context.actor.principal_ref,
            created_by=context.actor.principal_ref,
            title=_required_string(payload, "title"),
            revision=_optional_string(payload, "revision") or "1",
            status=KnowledgeStatus.REGISTERED,
            created_at=now,
            updated_at=now,
            metadata=_object(payload.get("metadata", {}), "metadata"),
        )
        stored = await providers.knowledge.register_source(
            source,
            _data_context(context, project_id=project_id),
        )
        return _knowledge_source_resource(stored)

    async def knowledge_update(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _, access = await _get_knowledge_source(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        if "title" not in payload and "metadata" not in payload:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "knowledge.update requires title and/or metadata",
            )
        title = _optional_string(payload, "title") if "title" in payload else None
        metadata = _object(payload.get("metadata"), "metadata") if "metadata" in payload else None
        source = await providers.knowledge.update_source(
            resource_ref,
            access,
            title=title,
            metadata=metadata,
        )
        return _knowledge_source_resource(source)

    async def knowledge_ingest(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _, access = await _get_knowledge_source(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        document = await providers.knowledge.ingest_source(
            resource_ref,
            _required_string(payload, "content"),
            _required_string(payload, "location"),
            access,
        )
        return _knowledge_document_resource(document)

    async def knowledge_reindex(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        _, access = await _get_knowledge_source(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        document = await providers.knowledge.reindex_source(
            resource_ref,
            _required_string(payload, "revision"),
            _required_string(payload, "content"),
            _required_string(payload, "location"),
            access,
        )
        return _knowledge_document_resource(document)

    async def knowledge_detach(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        _, access = await _get_knowledge_source(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        await providers.knowledge.remove_source(resource_ref, access)
        source = await providers.knowledge.get_source(resource_ref, access)
        return {**_knowledge_source_resource(source), "detached": True}

    async def knowledge_delete(
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del payload
        _, access = await _get_knowledge_source(
            providers,
            context,
            resource_ref,
            project_ids=known_project_ids,
        )
        await providers.knowledge.remove_source(resource_ref, access)
        source = await providers.knowledge.get_source(resource_ref, access)
        return {**_knowledge_source_resource(source), "deleted": True}

    return {
        "memory.create": memory_create,
        "memory.promote": memory_promote,
        "memory.update": memory_update,
        "memory.expire": memory_expire,
        "memory.delete": memory_delete,
        "knowledge.register": knowledge_register,
        "knowledge.update": knowledge_update,
        "knowledge.ingest": knowledge_ingest,
        "knowledge.reindex": knowledge_reindex,
        "knowledge.detach": knowledge_detach,
        "knowledge.delete": knowledge_delete,
    }


async def _get_memory(
    providers: DataProviderSet,
    context: RequestContext,
    memory_id: str,
    *,
    project_ids: ProjectIdProvider,
) -> tuple[MemoryEntry, DataAccessContext]:
    validate_id(memory_id, "memory")
    for project_id in _candidate_project_ids(project_ids):
        access = _data_context(context, project_id=project_id)
        try:
            return await providers.memory.get_entry(memory_id, access), access
        except ContractError as exc:
            if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                continue
            raise
    raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}")


async def _get_knowledge_source(
    providers: DataProviderSet,
    context: RequestContext,
    source_id: str,
    *,
    project_ids: ProjectIdProvider,
) -> tuple[KnowledgeSource, DataAccessContext]:
    validate_id(source_id, "knowledge_source")
    for project_id in _candidate_project_ids(project_ids):
        access = _data_context(context, project_id=project_id)
        try:
            return await providers.knowledge.get_source(source_id, access), access
        except ContractError as exc:
            if exc.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN, ErrorCode.UNAUTHORIZED}:
                continue
            raise
    raise ContractError(ErrorCode.NOT_FOUND, f"knowledge source not found: {source_id}")


def _candidate_project_ids(project_ids: ProjectIdProvider) -> tuple[str | None, ...]:
    return (None, *tuple(dict.fromkeys(project_ids())))


def _data_context(context: RequestContext, *, project_id: str | None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=project_id,
        ),
        actor_ref=context.actor.principal_ref,
    )


def _memory_scope(value: str) -> MemoryScope:
    try:
        return MemoryScope(value)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"unknown memory scope: {value}") from exc


def _memory_origin(value: str) -> MemoryOrigin:
    try:
        return MemoryOrigin(value)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"unknown memory origin: {value}") from exc


def _retention(payload: dict[str, JsonValue], scope: MemoryScope) -> RetentionPolicy:
    raw = payload.get("retention")
    if raw is None:
        return _default_retention(scope)
    if not isinstance(raw, str):
        raise ContractError(ErrorCode.INVALID_REQUEST, "retention must be a string")
    try:
        return RetentionPolicy(raw)
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"unknown retention policy: {raw}") from exc


def _default_retention(scope: MemoryScope) -> RetentionPolicy:
    if scope is MemoryScope.SHORT_TERM:
        return RetentionPolicy.EPHEMERAL
    if scope is MemoryScope.TASK:
        return RetentionPolicy.TASK_LIFETIME
    if scope is MemoryScope.WORKSPACE:
        return RetentionPolicy.PROJECT_LIFETIME
    if scope is MemoryScope.USER:
        return RetentionPolicy.USER_LIFETIME
    return RetentionPolicy.DURABLE


def _expires_at(payload: dict[str, JsonValue]) -> datetime | None:
    raw = payload.get("expires_at")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "expires_at must be an ISO timestamp or null"
        )
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST, "expires_at must be a valid ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(ErrorCode.INVALID_REQUEST, "expires_at must be timezone-aware")
    return parsed


def _project_id_for_memory_scope(
    payload: dict[str, JsonValue],
    scope: MemoryScope,
    scope_id: str,
) -> str | None:
    project_id = (
        scope_id if scope is MemoryScope.WORKSPACE else _optional_string(payload, "project_id")
    )
    if project_id is not None:
        validate_id(project_id, "project")
    return project_id


def _memory_owner_ref(context: RequestContext, scope: MemoryScope, scope_id: str) -> str:
    if scope is MemoryScope.USER:
        return f"user:{scope_id}"
    if scope is MemoryScope.ORGANIZATION:
        return f"organization:{scope_id}"
    return context.actor.principal_ref


def _provenance(payload: dict[str, JsonValue]) -> tuple[SourceRef, ...]:
    raw = payload.get("provenance", [])
    if not isinstance(raw, list):
        raise ContractError(ErrorCode.INVALID_REQUEST, "provenance must be an array")
    sources: list[SourceRef] = []
    for index, value in enumerate(raw):
        item = _object(value, f"provenance[{index}]")
        sources.append(
            SourceRef(
                kind=_required_string(item, "kind"),
                ref=_required_string(item, "ref"),
                location=_optional_string(item, "location"),
                revision=_optional_string(item, "revision"),
                checksum=_optional_string(item, "checksum"),
            )
        )
    return tuple(sources)


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string or null")
    return value


def _required_value(payload: dict[str, JsonValue], key: str) -> JsonValue:
    if key not in payload:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} is required")
    return payload[key]


def _object(value: JsonValue, field: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{field} must be an object")
    return value


def _memory_resource(entry: MemoryEntry) -> dict[str, JsonValue]:
    return {
        "id": entry.memory_id,
        "type": "memory",
        "scope": entry.scope.value,
        "scope_id": entry.scope_id,
        "owner_ref": entry.owner_ref,
        "created_by": entry.created_by,
        "created_at": entry.created_at.isoformat(),
        "value": entry.value,
        "origin": entry.origin.value,
        "retention": entry.retention.value,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at is not None else None,
        "provenance": [
            {
                "kind": item.kind,
                "ref": item.ref,
                "location": item.location,
                "revision": item.revision,
                "checksum": item.checksum,
            }
            for item in entry.provenance
        ],
        "supersedes_memory_id": entry.supersedes_memory_id,
        "classification": entry.classification,
        "metadata": dict(entry.metadata),
    }


def _knowledge_source_resource(source: KnowledgeSource) -> dict[str, JsonValue]:
    return {
        "id": source.source_id,
        "type": "knowledge-source",
        "project_id": source.project_id,
        "owner_ref": source.owner_ref,
        "created_by": source.created_by,
        "title": source.title,
        "revision": source.revision,
        "status": source.status.value,
        "created_at": source.created_at.isoformat(),
        "updated_at": source.updated_at.isoformat(),
        "content_checksum": source.content_checksum,
        "metadata": dict(source.metadata),
    }


def _knowledge_document_resource(document: KnowledgeDocument) -> dict[str, JsonValue]:
    return {
        "id": document.document_id,
        "type": "knowledge-document",
        "source_id": document.source_id,
        "revision": document.revision,
        "location": document.location,
        "checksum": document.checksum,
        "created_at": document.created_at.isoformat(),
    }


__all__ = [
    "DATA_CONTENT_COMMANDS",
    "KNOWLEDGE_COMMANDS",
    "MEMORY_COMMANDS",
    "data_command_handlers",
]
