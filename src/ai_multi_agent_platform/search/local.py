"""Dependency-free local SearchProvider for baseline and self-hosted use."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext, ProviderDescriptor

from .models import (
    SearchDocument,
    SearchMode,
    SearchPage,
    SearchQuery,
    SearchResult,
    decode_search_cursor,
    encode_search_cursor,
)
from .provider import SearchProvider


class LocalSearchProvider(SearchProvider):
    """Small in-memory derived index with deterministic keyword ranking."""

    def __init__(self, *, provider_id: str = "search_local") -> None:
        self._documents: dict[tuple[str, str], SearchDocument] = {}
        self._descriptor = ProviderDescriptor(
            provider_id=provider_id,
            provider_type="search",
            supported_operations=("exact", "keyword", "metadata", "rebuild"),
            resources={
                "modes": [
                    SearchMode.EXACT.value,
                    SearchMode.KEYWORD.value,
                    SearchMode.METADATA.value,
                ],
                "authoritative": False,
            },
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def upsert(self, document: SearchDocument, context: OperationContext) -> None:
        self._documents[document.key] = document

    async def delete(
        self,
        resource_type: str,
        resource_id: str,
        context: OperationContext,
    ) -> None:
        self._documents.pop((resource_type, resource_id), None)

    async def rebuild(
        self,
        documents: tuple[SearchDocument, ...],
        context: OperationContext,
    ) -> None:
        self._documents = {document.key: document for document in documents}

    async def search(self, query: SearchQuery, context: OperationContext) -> SearchPage:
        if query.mode in {SearchMode.SEMANTIC, SearchMode.HYBRID}:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"local search does not support {query.mode.value} mode",
            )

        candidates: list[SearchResult] = []
        for document in self._documents.values():
            if not _metadata_matches(document, query):
                continue
            result = _result_for_document(document, query)
            if result is not None:
                candidates.append(result)

        _sort_results(candidates, query)
        offset = decode_search_cursor(query.cursor)
        window = candidates[offset : offset + query.limit]
        next_offset = offset + len(window)
        next_cursor = encode_search_cursor(next_offset) if next_offset < len(candidates) else None
        return SearchPage(
            items=tuple(window),
            total=len(candidates),
            limit=query.limit,
            next_cursor=next_cursor,
        )


def _metadata_matches(document: SearchDocument, query: SearchQuery) -> bool:
    if query.exact_id is not None and document.resource_id != query.exact_id:
        return False
    if query.resource_types and document.resource_type not in query.resource_types:
        return False
    if query.project_id is not None and document.project_id != query.project_id:
        return False
    if query.workspace_id is not None and document.workspace_id != query.workspace_id:
        return False
    if query.statuses and document.status not in query.statuses:
        return False
    if query.tags and not set(query.tags).issubset(document.tags):
        return False
    if query.source_filters and document.source not in query.source_filters:
        return False
    if query.provider_filters and document.provider not in query.provider_filters:
        return False
    if query.updated_after is not None or query.updated_before is not None:
        updated_at = document.updated_at_datetime
        if updated_at is None:
            return False
        if query.updated_after is not None and updated_at < query.updated_after:
            return False
        if query.updated_before is not None and updated_at > query.updated_before:
            return False
    return True


def _result_for_document(document: SearchDocument, query: SearchQuery) -> SearchResult | None:
    relevance = 1.0 if query.exact_id == document.resource_id else 0.0
    matched: list[str] = ["resource_id"] if query.exact_id == document.resource_id else []

    if query.text is not None:
        needle = query.text.casefold()
        fields: tuple[tuple[str, str, float], ...] = (
            ("title", document.title, 4.0),
            ("resource_id", document.resource_id, 3.0),
            ("summary", document.summary, 2.0),
            ("tags", " ".join(document.tags), 1.5),
            ("keywords", " ".join(document.keywords), 1.5),
        )
        for field_name, value, weight in fields:
            if needle in value.casefold():
                relevance += weight
                matched.append(field_name)
        if not matched:
            return None
    elif query.mode is SearchMode.KEYWORD and query.exact_id is None:
        relevance = 0.0

    return SearchResult(
        resource_type=document.resource_type,
        resource_id=document.resource_id,
        title=document.title,
        summary=document.summary,
        project_id=document.project_id,
        workspace_id=document.workspace_id,
        owner_type=document.owner_type,
        owner_id=document.owner_id,
        status=document.status,
        tags=document.tags,
        relevance=relevance,
        matched_fields=tuple(dict.fromkeys(matched)),
        source=document.source,
        provider=document.provider,
        version=document.version,
        updated_at=document.updated_at,
        canonical_ref=document.canonical_ref,
        provenance=dict(document.provenance),
    )


def _sort_results(results: list[SearchResult], query: SearchQuery) -> None:
    reverse = query.direction == "desc"
    if query.sort == "id":
        results.sort(key=lambda item: (item.resource_type, item.resource_id), reverse=reverse)
        return
    if query.sort == "updated_at":
        results.sort(key=lambda item: item.updated_at or "", reverse=reverse)
        return
    results.sort(
        key=lambda item: (item.relevance, item.resource_type, item.resource_id),
        reverse=reverse,
    )
