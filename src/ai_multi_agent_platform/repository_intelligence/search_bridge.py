"""Scoped #45 federation over repository-intelligence text search.

This bridge deliberately does not copy repository/workspace source into the canonical global Search
index. It invokes the authorized repository-intelligence capability on demand, converts bounded hits
into ordinary SearchResult values, then applies the same post-provider authorization discipline used
by SearchService before exposing snippets or totals.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvoker,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.search import SearchMode, SearchPage, SearchQuery, SearchResult
from ai_multi_agent_platform.search.models import decode_search_cursor, encode_search_cursor

from .capabilities import RepositoryIntelligenceOperation

RepositorySearchAuthorizer = Callable[[SearchResult], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class RepositorySearchScope:
    repository_id: str
    revision: str = "HEAD"
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("repository search repository_id must not be blank")
        if not self.revision.strip():
            raise ValueError("repository search revision must not be blank")
        if self.workspace_id is not None and not self.workspace_id.strip():
            raise ValueError("repository search workspace_id must not be blank")


@dataclass(frozen=True, slots=True)
class RepositorySearchCaller:
    context: OperationContext
    trace: InvocationTrace
    granted_permissions: frozenset[str]
    available_worker_capabilities: frozenset[str] = frozenset()


class RepositoryIntelligenceSearchFederator:
    """Expose authorized source hits as scoped #45 results without persistent duplicate indexing."""

    def __init__(self, invoker: CapabilityInvoker, *, max_candidates: int = 500) -> None:
        if not 1 <= max_candidates <= 500:
            raise ValueError("repository search max_candidates must be between 1 and 500")
        self._invoker = invoker
        self._max_candidates = max_candidates

    async def search(
        self,
        scope: RepositorySearchScope,
        query: SearchQuery,
        caller: RepositorySearchCaller,
        authorize: RepositorySearchAuthorizer,
    ) -> SearchPage:
        if query.mode not in {SearchMode.EXACT, SearchMode.KEYWORD}:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "repository-intelligence federation currently supports exact/keyword search only",
            )
        if query.text is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "repository-intelligence federation requires query text",
            )
        if query.project_id is not None and query.project_id != caller.context.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository search project scope differs from invocation scope",
            )
        if query.workspace_id is not None and query.workspace_id != scope.workspace_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository search Workspace scope differs from repository scope",
            )

        offset = decode_search_cursor(query.cursor)
        requested_candidates = min(self._max_candidates, max(query.limit + offset, query.limit))
        result = await self._invoker.invoke(
            CapabilityInvocation(
                invocation_id=f"{caller.trace.run_id}:repository-search",
                capability_id=RepositoryIntelligenceOperation.TEXT_SEARCH.value,
                arguments={
                    "repository_id": scope.repository_id,
                    "revision": scope.revision,
                    "query": query.text,
                    "case_sensitive": query.mode is SearchMode.EXACT,
                    "max_results": requested_candidates,
                },
                context=caller.context,
                trace=caller.trace,
                granted_permissions=caller.granted_permissions,
                available_worker_capabilities=caller.available_worker_capabilities,
            )
        )
        candidates = _project_hits(scope, result, caller.context.project_id)

        if query.provider_filters and result.provider_id not in query.provider_filters:
            candidates = ()

        authorized: list[SearchResult] = []
        for candidate in candidates:
            if await authorize(candidate):
                authorized.append(_authorized(candidate))

        page_items = authorized[offset : offset + query.limit]
        next_offset = offset + len(page_items)
        next_cursor = encode_search_cursor(next_offset) if next_offset < len(authorized) else None
        return SearchPage(
            items=tuple(page_items),
            total=len(authorized),
            limit=query.limit,
            next_cursor=next_cursor,
        )


def _project_hits(
    scope: RepositorySearchScope,
    result: CapabilityInvocationResult,
    project_id: str | None,
) -> tuple[SearchResult, ...]:
    output = result.output
    if not isinstance(output, dict):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository search provider output must be an object",
            provider_id=result.provider_id,
        )
    provenance = output.get("provenance")
    hits = output.get("hits")
    if not isinstance(provenance, dict) or not isinstance(hits, list):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository search provider output lacks hits/provenance",
            provider_id=result.provider_id,
        )
    if provenance.get("repository_id") != scope.repository_id:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository search provenance repository mismatch",
            provider_id=result.provider_id,
        )
    if provenance.get("requested_revision") != scope.revision:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository search provenance revision mismatch",
            provider_id=result.provider_id,
        )
    resolved_revision = provenance.get("resolved_revision")
    freshness = provenance.get("freshness")
    if not isinstance(resolved_revision, str):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository search provenance lacks resolved revision",
            provider_id=result.provider_id,
        )

    projected: list[SearchResult] = []
    for index, hit in enumerate(hits):
        if not isinstance(hit, dict):
            continue
        path = hit.get("path")
        line = hit.get("line")
        text = hit.get("text")
        if (
            not isinstance(path, str)
            or not isinstance(line, int)
            or isinstance(line, bool)
            or not isinstance(text, str)
        ):
            continue
        projected.append(
            SearchResult(
                resource_type="repository_source_hit",
                resource_id=f"{scope.repository_id}:{resolved_revision}:{path}:{line}",
                title=f"{path}:{line}",
                summary=text,
                project_id=project_id,
                workspace_id=scope.workspace_id,
                owner_type=None,
                owner_id=None,
                status=None,
                tags=("repository", "source", "repository-intelligence"),
                relevance=max(0.0, 1.0 - (index / max(1, len(hits)))),
                matched_fields=("source",),
                source="repository-intelligence",
                provider=result.provider_id,
                version=result.capability_version,
                updated_at=None,
                canonical_ref=None,
                provenance={
                    "repository_id": scope.repository_id,
                    "requested_revision": scope.revision,
                    "resolved_revision": resolved_revision,
                    "freshness": freshness,
                    "intelligence_provider_id": result.provider_id,
                    "path": path,
                    "line": line,
                    "derived_search_result": True,
                },
            )
        )
    return tuple(projected)


def _authorized(value: SearchResult) -> SearchResult:
    return SearchResult(
        resource_type=value.resource_type,
        resource_id=value.resource_id,
        title=value.title,
        summary=value.summary,
        project_id=value.project_id,
        workspace_id=value.workspace_id,
        owner_type=value.owner_type,
        owner_id=value.owner_id,
        status=value.status,
        tags=value.tags,
        relevance=value.relevance,
        matched_fields=value.matched_fields,
        source=value.source,
        provider=value.provider,
        version=value.version,
        updated_at=value.updated_at,
        canonical_ref=value.canonical_ref,
        provenance=dict(value.provenance),
        access="authorized",
        redacted=value.redacted,
    )


__all__ = [
    "RepositoryIntelligenceSearchFederator",
    "RepositorySearchAuthorizer",
    "RepositorySearchCaller",
    "RepositorySearchScope",
]
