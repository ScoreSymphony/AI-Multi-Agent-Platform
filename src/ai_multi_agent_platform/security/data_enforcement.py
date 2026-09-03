"""Authorization wrappers for the refined issue-#13 data-provider contracts."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

from ai_multi_agent_platform.contracts import (
    JsonValue,
    KnowledgeHit,
    KnowledgeQuery,
    OperationContext,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.data.contracts import FileProvider, KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    FileRecord,
    IndexReference,
    KnowledgeDocument,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
    KnowledgeSource,
    MemoryEntry,
    MemoryQuery,
    OrphanReport,
)

from .authorization import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate


def _data_action(
    context: DataAccessContext,
    *,
    action: AuthorizationAction,
    resource_type: ResourceType,
    resource_id: str,
    payload: JsonValue = None,
    side_effect: str | None = None,
) -> ProposedAction:
    labels = (context.classification,) if context.classification is not None else ()
    return ProposedAction(
        AuthorizationContext(
            actor=infer_actor_identity(context.actor_ref),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            operation=context.operation,
            task_id=context.task_id,
            run_id=context.run_id,
            agent_id=context.agent_id,
            side_effect=side_effect,
            security_labels=labels,
            trust_context=context.audit_metadata,
        ),
        payload=payload,
    )


def _compat_data_context(operation: OperationContext) -> DataAccessContext:
    if operation.owner_type is not None and operation.owner_id is not None:
        actor_ref = f"{operation.owner_type}:{operation.owner_id}"
    else:
        actor_ref = "service:platform"
    return DataAccessContext(operation=operation, actor_ref=actor_ref)


class AuthorizedDataFileProvider(FileProvider):
    """Protect every refined and inherited core file/artifact operation."""

    def __init__(self, inner: FileProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def create_file(
        self,
        data: bytes,
        context: DataAccessContext,
        *,
        file_id: str | None = None,
        content_type: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> FileRecord:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.CREATE,
                resource_type=ResourceType.FILE,
                resource_id=file_id or "file:new",
                payload={
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "content_type": content_type,
                    "metadata": metadata or {},
                },
                side_effect="file_create",
            )
        )
        return await self._inner.create_file(
            data,
            context,
            file_id=file_id,
            content_type=content_type,
            metadata=metadata,
        )

    async def get_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.FILE,
                resource_id=file_id,
            )
        )
        return await self._inner.get_file(file_id, context)

    async def list_files(self, context: DataAccessContext) -> tuple[FileRecord, ...]:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.VIEW,
                resource_type=ResourceType.FILE,
                resource_id="file:*",
            )
        )
        return await self._inner.list_files(context)

    def stream_file(
        self,
        file_id: str,
        context: DataAccessContext,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        async def iterator() -> AsyncIterator[bytes]:
            await self._gate.enforce(
                _data_action(
                    context,
                    action=AuthorizationAction.READ,
                    resource_type=ResourceType.FILE,
                    resource_id=file_id,
                )
            )
            async for chunk in self._inner.stream_file(file_id, context, chunk_size=chunk_size):
                yield chunk

        return iterator()

    async def delete_file(self, file_id: str, context: DataAccessContext) -> FileRecord:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.DELETE,
                resource_type=ResourceType.FILE,
                resource_id=file_id,
                side_effect="file_delete",
            )
        )
        return await self._inner.delete_file(file_id, context)

    async def verify_checksum(self, file_id: str, context: DataAccessContext) -> bool:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.FILE,
                resource_id=file_id,
            )
        )
        return await self._inner.verify_checksum(file_id, context)

    async def link_artifact(
        self,
        file_id: str,
        artifact_id: str,
        context: DataAccessContext,
    ) -> FileRecord:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.FILE,
                resource_id=file_id,
                payload={"artifact_id": artifact_id},
                side_effect="artifact_link",
            )
        )
        return await self._inner.link_artifact(file_id, artifact_id, context)

    async def detect_orphans(self, context: DataAccessContext) -> OrphanReport:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.FILE,
                resource_id="file:orphans",
            )
        )
        return await self._inner.detect_orphans(context)

    async def write(
        self,
        object_ref: str,
        data: bytes,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.FILE,
                resource_id=object_ref,
                payload={
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                    "metadata": metadata or {},
                },
                side_effect="legacy_file_write",
            )
        )
        return await self._inner.write(object_ref, data, context, metadata=metadata)

    async def read(self, object_ref: str, context: OperationContext) -> bytes:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.READ,
                resource_type=ResourceType.FILE,
                resource_id=object_ref,
            )
        )
        return await self._inner.read(object_ref, context)


class AuthorizedDataMemoryProvider(MemoryProvider):
    """Protect every refined and inherited core scoped-memory operation."""

    def __init__(self, inner: MemoryProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.CREATE,
                resource_type=ResourceType.MEMORY,
                resource_id=entry.memory_id,
                payload={
                    "scope": entry.scope.value,
                    "scope_id": entry.scope_id,
                    "value": entry.value,
                    "retention": entry.retention.value,
                    "classification": entry.classification,
                    "metadata": entry.metadata,
                },
                side_effect="memory_write",
            )
        )
        return await self._inner.write_entry(entry, context)

    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.MEMORY,
                resource_id=memory_id,
            )
        )
        return await self._inner.get_entry(memory_id, context)

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.MEMORY,
                resource_id=f"memory:{query.scope.value}:{query.scope_id}",
                payload={
                    "owner_ref": query.owner_ref,
                    "include_expired": query.include_expired,
                    "include_superseded": query.include_superseded,
                    "limit": query.limit,
                },
            )
        )
        return await self._inner.query_entries(query, context)

    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.MEMORY,
                resource_id=f"memory:{query.scope.value}:{query.scope_id}",
                payload={"text": text, "limit": query.limit},
            )
        )
        return await self._inner.search_entries(query, text, context)

    async def supersede_entry(
        self,
        memory_id: str,
        replacement: MemoryEntry,
        context: DataAccessContext,
    ) -> MemoryEntry:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.MEMORY,
                resource_id=memory_id,
                payload={
                    "replacement_id": replacement.memory_id,
                    "value": replacement.value,
                    "metadata": replacement.metadata,
                },
                side_effect="memory_supersede",
            )
        )
        return await self._inner.supersede_entry(memory_id, replacement, context)

    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.DELETE,
                resource_type=ResourceType.MEMORY,
                resource_id=memory_id,
                side_effect="memory_delete",
            )
        )
        await self._inner.delete_entry(memory_id, context)

    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.MEMORY,
                resource_id="memory:expiry",
                side_effect="memory_expire",
            )
        )
        return await self._inner.expire_entries(context)

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.MEMORY,
                resource_id=f"{namespace}:{key}",
                payload={"value": value, "metadata": metadata or {}},
                side_effect="legacy_memory_write",
            )
        )
        return await self._inner.put(namespace, key, value, context, metadata=metadata)

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.READ,
                resource_type=ResourceType.MEMORY,
                resource_id=f"{namespace}:{key}",
            )
        )
        return await self._inner.get(namespace, key, context)


class AuthorizedDataKnowledgeProvider(KnowledgeProvider):
    """Protect every refined and inherited core knowledge-source operation."""

    def __init__(self, inner: KnowledgeProvider, gate: AuthorizationGate) -> None:
        self._inner = inner
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    async def register_source(
        self,
        source: KnowledgeSource,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.CREATE,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source.source_id,
                payload={
                    "title": source.title,
                    "revision": source.revision,
                    "content_checksum": source.content_checksum,
                    "metadata": source.metadata,
                },
                side_effect="knowledge_register",
            )
        )
        return await self._inner.register_source(source, context)

    async def ingest_source(
        self,
        source_id: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
                payload={
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "location": location,
                },
                side_effect="knowledge_ingest",
            )
        )
        return await self._inner.ingest_source(source_id, content, location, context)

    async def get_index_status(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> IndexReference:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
            )
        )
        return await self._inner.get_index_status(source_id, context)

    async def search(
        self,
        request: KnowledgeSearchRequest,
    ) -> tuple[KnowledgeSearchResult, ...]:
        await self._gate.enforce(
            _data_action(
                request.context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id="knowledge:*",
                payload={
                    "query": request.query,
                    "source_ids": list(request.source_ids),
                    "mode": request.mode.value,
                    "limit": request.limit,
                },
            )
        )
        return await self._inner.search(request)

    async def reindex_source(
        self,
        source_id: str,
        revision: str,
        content: str,
        location: str,
        context: DataAccessContext,
    ) -> KnowledgeDocument:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
                payload={
                    "revision": revision,
                    "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                    "location": location,
                },
                side_effect="knowledge_reindex",
            )
        )
        return await self._inner.reindex_source(source_id, revision, content, location, context)

    async def remove_source(self, source_id: str, context: DataAccessContext) -> None:
        await self._gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.DELETE,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
                side_effect="knowledge_remove",
            )
        )
        await self._inner.remove_source(source_id, context)

    async def index(
        self,
        source_ref: str,
        content: str,
        context: OperationContext,
    ) -> StoredObject:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_ref,
                payload={"content_sha256": hashlib.sha256(content.encode()).hexdigest()},
                side_effect="legacy_knowledge_write",
            )
        )
        return await self._inner.index(source_ref, content, context)

    async def query(self, request: KnowledgeQuery) -> tuple[KnowledgeHit, ...]:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(request.context),
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id="knowledge:*",
                payload={"query": request.query, "filters": request.filters},
            )
        )
        return await self._inner.query(request)

    async def get(self, source_ref: str, context: OperationContext) -> KnowledgeHit:
        await self._gate.enforce(
            _data_action(
                _compat_data_context(context),
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_ref,
            )
        )
        return await self._inner.get(source_ref, context)
