"""Checkpoint-aware synchronization for the canonical Search Control Plane (#45)."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext, OperationControl
from ai_multi_agent_platform.search import (
    SEARCH_INDEX_SCHEMA_VERSION,
    SearchIndexCheckpoint,
    SearchQuery,
)

from .models import RequestContext
from .registered_search_contract import ControlPlane as _RegisteredSearchControlPlane


class ControlPlane(_RegisteredSearchControlPlane):
    """Add optional checkpointed Search refresh without weakening the safe default.

    The correctness-first default remains rebuild-before-query. Deployments whose
    provider is kept synchronized through canonical write-through/event delivery may
    explicitly switch to checkpointed refresh after Control Plane construction. A
    missing, stale or incompatible checkpoint always forces recovery before Search
    results are served.
    """

    @property
    def search_rebuild_before_query(self) -> bool:
        return getattr(self, "_search_rebuild_before_query", True)

    def configure_search_refresh(self, *, rebuild_before_query: bool) -> None:
        """Choose correctness-first or checkpointed refresh after composition setup.

        Configuration is intentionally outside ``__init__`` so the option does not have
        to propagate through every cooperative Control Plane mixin constructor.
        """

        if not isinstance(rebuild_before_query, bool):
            raise TypeError("rebuild_before_query must be a boolean")
        self._search_rebuild_before_query = rebuild_before_query

    async def search_index_checkpoint(
        self,
        *,
        correlation_id: str = "search-checkpoint",
    ) -> SearchIndexCheckpoint | None:
        """Return provider synchronization metadata when checkpointing is supported."""

        return await self.search_provider.index_checkpoint(
            OperationContext(correlation_id=correlation_id)
        )

    async def mark_search_index_stale(
        self,
        reason: str,
        *,
        correlation_id: str = "search-stale",
    ) -> None:
        """Record a missed-event/reconciliation condition on capable providers."""

        if not reason.strip():
            raise ValueError("search stale reason must not be blank")
        await self.search_provider.mark_stale(
            reason,
            OperationContext(correlation_id=correlation_id),
        )

    async def search_resources(
        self,
        context: RequestContext,
        query: SearchQuery,
    ) -> dict[str, JsonValue]:
        await self._ensure_search_index(correlation_id=context.correlation_id)
        operation = OperationContext(
            correlation_id=context.correlation_id,
            owner_type=context.actor.owner_type,
            owner_id=context.actor.owner_id,
            project_id=query.project_id,
            control=OperationControl(),
        )
        page = await self._search_service.search(
            query,
            operation,
            lambda result: self._search_result_allowed(context, result),
        )
        return page.to_json()

    async def _ensure_search_index(self, *, correlation_id: str) -> None:
        if self.search_rebuild_before_query:
            await self.rebuild_search_index(correlation_id=correlation_id)
            return

        operation = OperationContext(correlation_id=correlation_id)
        checkpoint = await self.search_provider.index_checkpoint(operation)
        if checkpoint is None:
            await self.rebuild_search_index(correlation_id=correlation_id)
            checkpoint = await self.search_provider.index_checkpoint(operation)
            # Providers without checkpoint support retain correctness by rebuilding
            # before every query even when checkpointed mode was requested.
            if checkpoint is None:
                return
        elif checkpoint.stale or checkpoint.schema_version != SEARCH_INDEX_SCHEMA_VERSION:
            await self.rebuild_search_index(correlation_id=correlation_id)
            checkpoint = await self.search_provider.index_checkpoint(operation)
            if checkpoint is None:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "search provider lost checkpoint capability during recovery",
                )

        if checkpoint.stale:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "search provider remained stale after recovery rebuild",
                details={
                    "generation": checkpoint.generation,
                    "stale_reason": checkpoint.stale_reason,
                },
            )
        if checkpoint.schema_version != SEARCH_INDEX_SCHEMA_VERSION:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "search provider index schema is incompatible",
                details={
                    "expected_schema_version": SEARCH_INDEX_SCHEMA_VERSION,
                    "provider_schema_version": checkpoint.schema_version,
                },
            )


__all__ = ["ControlPlane"]
