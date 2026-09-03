"""Authorization-safe application service over replaceable SearchProviders."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext

from .models import (
    SearchPage,
    SearchQuery,
    SearchResult,
    decode_search_cursor,
    encode_search_cursor,
)
from .provider import SearchProvider

SearchAuthorizer = Callable[[SearchResult], Awaitable[bool]]


class SearchService:
    """Filter provider candidates before exposing results, counts or snippets."""

    def __init__(self, provider: SearchProvider, *, max_candidates: int = 10_000) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be positive")
        self._provider = provider
        self._max_candidates = max_candidates

    @property
    def provider(self) -> SearchProvider:
        return self._provider

    async def search(
        self,
        query: SearchQuery,
        context: OperationContext,
        authorize: SearchAuthorizer,
    ) -> SearchPage:
        """Return an authorization-safe page without leaking provider totals.

        The baseline scans provider pages from the beginning, applies canonical
        authorization, and only then paginates. This favors correctness/privacy
        over scale and can later be optimized by providers that advertise safe
        pre-filtering capabilities.
        """

        authorized: list[SearchResult] = []
        provider_cursor: str | None = None
        seen_cursors: set[str] = set()
        scanned = 0

        while True:
            provider_query = replace(query, cursor=provider_cursor, limit=200)
            page = await self._provider.search(provider_query, context)
            scanned += len(page.items)
            if scanned > self._max_candidates:
                raise ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "search candidate scan limit exceeded",
                    details={"max_candidates": self._max_candidates},
                )

            for item in page.items:
                if await authorize(item):
                    authorized.append(replace(item, access="authorized"))

            if page.next_cursor is None:
                break
            if page.next_cursor in seen_cursors:
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "search provider returned a repeating cursor",
                )
            seen_cursors.add(page.next_cursor)
            provider_cursor = page.next_cursor

        offset = decode_search_cursor(query.cursor)
        window = authorized[offset : offset + query.limit]
        next_offset = offset + len(window)
        next_cursor = encode_search_cursor(next_offset) if next_offset < len(authorized) else None
        return SearchPage(
            items=tuple(window),
            total=len(authorized),
            limit=query.limit,
            next_cursor=next_cursor,
        )
