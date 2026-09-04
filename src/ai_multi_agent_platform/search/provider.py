"""Replaceable search-provider boundary."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import OperationContext

from .checkpoint import SearchIndexCheckpoint
from .models import SearchDocument, SearchPage, SearchQuery


class SearchProvider(ProviderContract):
    """Derived discovery index behind the canonical search contract.

    Implementations may use relational text indexes, dedicated full-text engines
    or vector stores. They do not own canonical resource state and must support a
    complete rebuild from canonical sources.

    Checkpoint reporting is optional so existing provider adapters remain compatible.
    Providers that implement it enable checkpointed/event-driven synchronization and
    explicit stale-index recovery without changing canonical Search identity semantics.
    """

    @abstractmethod
    async def search(self, query: SearchQuery, context: OperationContext) -> SearchPage: ...

    @abstractmethod
    async def upsert(self, document: SearchDocument, context: OperationContext) -> None: ...

    @abstractmethod
    async def delete(
        self,
        resource_type: str,
        resource_id: str,
        context: OperationContext,
    ) -> None: ...

    @abstractmethod
    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        """Atomically replace derived index contents with canonical documents."""

    async def index_checkpoint(
        self,
        context: OperationContext,
    ) -> SearchIndexCheckpoint | None:
        """Return derived synchronization metadata when the provider supports it.

        ``None`` means checkpoint capability is unavailable. Callers must then retain
        correctness-first rebuild behavior rather than assuming the index is fresh.
        """

        del context
        return None

    async def mark_stale(self, reason: str, context: OperationContext) -> None:
        """Mark provider state stale when supported.

        The default is intentionally a no-op for backward compatibility. A caller that
        requires checkpointed synchronization must verify ``index_checkpoint()`` before
        relying on freshness.
        """

        del reason, context
