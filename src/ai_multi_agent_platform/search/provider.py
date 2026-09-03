"""Replaceable search-provider boundary."""

from __future__ import annotations

from abc import abstractmethod

from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import SearchDocument, SearchPage, SearchQuery


class SearchProvider(ProviderContract):
    """Derived discovery index behind the canonical search contract.

    Implementations may use relational text indexes, dedicated full-text engines
    or vector stores. They do not own canonical resource state and must support a
    complete rebuild from canonical sources.
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
