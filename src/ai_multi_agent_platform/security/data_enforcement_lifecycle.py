"""Issue #251 authorization extensions for canonical Memory/Knowledge content."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import JsonValue, ProviderDescriptor
from ai_multi_agent_platform.data.contracts import KnowledgeProvider, MemoryProvider
from ai_multi_agent_platform.data.models import (
    DataAccessContext,
    KnowledgeSource,
    MemoryEntry,
    MemoryQuery,
)

from .authorization import AuthorizationAction, ResourceType
from .data_enforcement import (
    AuthorizedDataKnowledgeProvider as _BaseAuthorizedDataKnowledgeProvider,
)
from .data_enforcement import AuthorizedDataMemoryProvider as _BaseAuthorizedDataMemoryProvider
from .data_enforcement import _data_action
from .enforcement import AuthorizationGate


class AuthorizedDataMemoryProvider(_BaseAuthorizedDataMemoryProvider):
    """#15 Memory enforcement including the canonical #251 lifecycle semantics."""

    def __init__(self, inner: MemoryProvider, gate: AuthorizationGate) -> None:
        super().__init__(inner, gate)
        self._lifecycle_inner = inner
        self._lifecycle_gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._lifecycle_inner.descriptor

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        await self._lifecycle_gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.CREATE,
                resource_type=ResourceType.MEMORY,
                resource_id=entry.memory_id,
                payload={
                    "scope": entry.scope.value,
                    "scope_id": entry.scope_id,
                    "value": entry.value,
                    "origin": entry.origin.value,
                    "retention": entry.retention.value,
                    "classification": entry.classification,
                    "metadata": entry.metadata,
                },
                side_effect="memory_write",
            )
        )
        return await self._lifecycle_inner.write_entry(entry, context)

    async def expire_entry(
        self,
        memory_id: str,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> MemoryEntry:
        await self._lifecycle_gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.MEMORY,
                resource_id=memory_id,
                payload={"scope": query.scope.value, "scope_id": query.scope_id},
                side_effect="memory_expire",
            )
        )
        return await self._lifecycle_inner.expire_entry(memory_id, query, context)


class AuthorizedDataKnowledgeProvider(_BaseAuthorizedDataKnowledgeProvider):
    """#15 Knowledge enforcement including canonical #251 source discovery/management."""

    def __init__(self, inner: KnowledgeProvider, gate: AuthorizationGate) -> None:
        super().__init__(inner, gate)
        self._lifecycle_inner = inner
        self._lifecycle_gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._lifecycle_inner.descriptor

    async def get_source(
        self,
        source_id: str,
        context: DataAccessContext,
    ) -> KnowledgeSource:
        await self._lifecycle_gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.READ,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
            )
        )
        return await self._lifecycle_inner.get_source(source_id, context)

    async def list_sources(
        self,
        context: DataAccessContext,
    ) -> tuple[KnowledgeSource, ...]:
        await self._lifecycle_gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.VIEW,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id="knowledge:*",
            )
        )
        return await self._lifecycle_inner.list_sources(context)

    async def update_source(
        self,
        source_id: str,
        context: DataAccessContext,
        *,
        title: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> KnowledgeSource:
        await self._lifecycle_gate.enforce(
            _data_action(
                context,
                action=AuthorizationAction.MODIFY,
                resource_type=ResourceType.KNOWLEDGE_SOURCE,
                resource_id=source_id,
                payload={"title": title, "metadata": metadata},
                side_effect="knowledge_metadata_update",
            )
        )
        return await self._lifecycle_inner.update_source(
            source_id,
            context,
            title=title,
            metadata=metadata,
        )


__all__ = [
    "AuthorizedDataKnowledgeProvider",
    "AuthorizedDataMemoryProvider",
]
