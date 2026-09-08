"""Minimal-source context funnel over canonical repository-intelligence capabilities.

The funnel is consumer-neutral: coding agents, planners, reviewers and research workers can all use
it through the normal #12 invocation boundary. It never reads a repository path directly and never
turns provider summaries into instruction authority. Selection uses bounded text-search evidence;
only bounded exact source slices become Context candidates for #590 assembly.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    InvocationTrace,
)
from ai_multi_agent_platform.context import (
    ContextCandidate,
    ContextDataClassification,
    ContextEntryRole,
    ContextFreshness,
    ContextSourceRef,
    ContextSourceRequest,
    ContextSourceType,
    ContextTrust,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext

from .capabilities import RepositoryIntelligenceOperation
from .fallback import CapabilityInvocationPort

_CURRENT_FRESHNESS = frozenset(
    {"live_revision", "workspace_snapshot", "live_workspace", "fresh_index"}
)


@dataclass(frozen=True, slots=True)
class RepositoryContextRequest:
    repository_id: str
    query: str
    revision: str = "HEAD"
    max_hits: int = 20
    max_slices: int = 6
    slice_radius_lines: int = 8
    mandatory: bool = False
    data_classification: ContextDataClassification = ContextDataClassification.INTERNAL

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("repository context repository_id must not be blank")
        if not self.query.strip():
            raise ValueError("repository context query must not be blank")
        if not self.revision.strip():
            raise ValueError("repository context revision must not be blank")
        if not 1 <= self.max_hits <= 100:
            raise ValueError("repository context max_hits must be between 1 and 100")
        if not 1 <= self.max_slices <= 20:
            raise ValueError("repository context max_slices must be between 1 and 20")
        if not 0 <= self.slice_radius_lines <= 50:
            raise ValueError("repository context slice_radius_lines must be between 0 and 50")


@dataclass(frozen=True, slots=True)
class RepositoryContextCaller:
    context: OperationContext
    trace: InvocationTrace
    granted_permissions: frozenset[str]
    available_worker_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.trace.correlation_id != self.context.correlation_id:
            raise ValueError("repository context trace/context correlation mismatch")
        if self.trace.project_id != self.context.project_id:
            raise ValueError("repository context trace/context project mismatch")


@dataclass(frozen=True, slots=True)
class RepositoryContextFunnelReport:
    candidates: tuple[ContextCandidate, ...]
    search_provider_id: str
    slice_provider_ids: tuple[str, ...]
    tool_calls: int
    selected_hits: int
    source_slices: int
    source_bytes: int
    broad_full_file_reads: int = 0


class RepositoryContextFunnel:
    """Narrow repository search -> exact source slices through a canonical invocation port."""

    def __init__(self, invoker: CapabilityInvocationPort) -> None:
        self._invoker = invoker

    async def collect(
        self,
        request: RepositoryContextRequest,
        caller: RepositoryContextCaller,
    ) -> RepositoryContextFunnelReport:
        search = await self._invoke(
            caller,
            ordinal=0,
            operation=RepositoryIntelligenceOperation.TEXT_SEARCH,
            arguments={
                "repository_id": request.repository_id,
                "revision": request.revision,
                "query": request.query,
                "max_results": request.max_hits,
            },
        )
        search_output = _object(search.output, "repository text-search output")
        search_provenance = _provenance(search_output)
        _validate_source_identity(
            search_provenance,
            repository_id=request.repository_id,
            requested_revision=request.revision,
        )
        hits = search_output.get("hits")
        if not isinstance(hits, list):
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "repository text-search output has no hits array",
                provider_id=search.provider_id,
            )

        selected: list[tuple[str, int]] = []
        seen_paths: set[str] = set()
        for raw_hit in hits:
            if len(selected) >= request.max_slices:
                break
            if not isinstance(raw_hit, dict):
                continue
            path = raw_hit.get("path")
            line = raw_hit.get("line")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(line, int) or isinstance(line, bool) or line < 1:
                continue
            if path in seen_paths:
                continue
            seen_paths.add(path)
            selected.append((path, line))

        candidates: list[ContextCandidate] = []
        slice_provider_ids: list[str] = []
        source_bytes = 0
        for index, (path, line) in enumerate(selected, start=1):
            start_line = max(1, line - request.slice_radius_lines)
            end_line = line + request.slice_radius_lines
            sliced = await self._invoke(
                caller,
                ordinal=index,
                operation=RepositoryIntelligenceOperation.SOURCE_SLICE,
                arguments={
                    "repository_id": request.repository_id,
                    "revision": request.revision,
                    "path": path,
                    "start_line": start_line,
                    "end_line": end_line,
                },
            )
            output = _object(sliced.output, "repository source-slice output")
            provenance = _provenance(output)
            _validate_source_identity(
                provenance,
                repository_id=request.repository_id,
                requested_revision=request.revision,
            )
            if provenance.get("resolved_revision") != search_provenance.get("resolved_revision"):
                raise ContractError(
                    ErrorCode.INVALID_PROVIDER_RESPONSE,
                    "repository context funnel changed resolved revision between search and slice",
                    provider_id=sliced.provider_id,
                )
            content = _render_slice(output)
            if not content:
                continue
            source_bytes += len(content.encode("utf-8"))
            slice_provider_ids.append(sliced.provider_id)
            workspace = provenance.get("workspace")
            workspace_id = workspace.get("workspace_id") if isinstance(workspace, dict) else None
            if workspace_id is not None and not isinstance(workspace_id, str):
                workspace_id = None
            resolved_revision = provenance.get("resolved_revision")
            freshness = provenance.get("freshness")
            candidates.append(
                ContextCandidate(
                    # Repository paths are not canonical #30 File identities. Keep this as generic
                    # result evidence so #590 authorization does not mistake a provider path for a
                    # platform File lifecycle ID. Exact source identity remains in provenance below.
                    source=ContextSourceRef(
                        source_type=ContextSourceType.RESULT,
                        source_id=f"repository-source:{request.repository_id}:{path}",
                        revision=resolved_revision if isinstance(resolved_revision, str) else None,
                        locator=path,
                    ),
                    role=ContextEntryRole.EVIDENCE,
                    selection_reason=(
                        "bounded repository-intelligence text hit followed by exact source slice"
                    ),
                    mandatory=request.mandatory,
                    inline_content=content,
                    freshness=_context_freshness(freshness),
                    trust=(
                        ContextTrust.TRUSTED
                        if sliced.provider_id.startswith("platform.repository-intelligence.")
                        else ContextTrust.UNTRUSTED
                    ),
                    data_classification=request.data_classification,
                    priority=max(0, request.max_slices - index + 1),
                    relevance=max(0.0, 1.0 - ((index - 1) / max(1, request.max_slices))),
                    project_id=caller.context.project_id,
                    workspace_id=workspace_id,
                    metadata={
                        "repository_id": request.repository_id,
                        "requested_revision": request.revision,
                        "resolved_revision": resolved_revision,
                        "selection_provider_id": search.provider_id,
                        "source_provider_id": sliced.provider_id,
                        "freshness": freshness,
                        "path": path,
                        "matched_line": line,
                        "source_slice": True,
                        "provider_summary_authority": False,
                    },
                )
            )

        return RepositoryContextFunnelReport(
            candidates=tuple(candidates),
            search_provider_id=search.provider_id,
            slice_provider_ids=tuple(slice_provider_ids),
            tool_calls=1 + len(selected),
            selected_hits=len(selected),
            source_slices=len(candidates),
            source_bytes=source_bytes,
        )

    async def _invoke(
        self,
        caller: RepositoryContextCaller,
        *,
        ordinal: int,
        operation: RepositoryIntelligenceOperation,
        arguments: dict[str, JsonValue],
    ) -> CapabilityInvocationResult:
        return await self._invoker.invoke(
            CapabilityInvocation(
                invocation_id=(
                    f"{caller.trace.run_id}:repository-intelligence:{ordinal}:{operation.value}"
                ),
                capability_id=operation.value,
                arguments=arguments,
                context=caller.context,
                trace=caller.trace,
                granted_permissions=caller.granted_permissions,
                available_worker_capabilities=caller.available_worker_capabilities,
            )
        )


RepositoryContextRequestResolver = Callable[
    [ContextSourceRequest], Awaitable[tuple[RepositoryContextRequest, RepositoryContextCaller] | None]
]


class RepositoryIntelligenceContextSourceAdapter:
    """Structural #590 source adapter for Agent, Planner, Reviewer and Research compositions."""

    adapter_id = "repository-intelligence-context/v1"

    def __init__(
        self,
        funnel: RepositoryContextFunnel,
        request_resolver: RepositoryContextRequestResolver,
    ) -> None:
        self._funnel = funnel
        self._request_resolver = request_resolver

    async def collect(self, request: ContextSourceRequest) -> tuple[ContextCandidate, ...]:
        resolved = await self._request_resolver(request)
        if resolved is None:
            return ()
        repository_request, caller = resolved
        report = await self._funnel.collect(repository_request, caller)
        return report.candidates


def _object(value: JsonValue, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ContractError(ErrorCode.INVALID_PROVIDER_RESPONSE, f"{name} must be an object")
    return value


def _provenance(output: dict[str, JsonValue]) -> dict[str, JsonValue]:
    value = output.get("provenance")
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository-intelligence source output lacks provenance",
        )
    return value


def _validate_source_identity(
    provenance: dict[str, JsonValue],
    *,
    repository_id: str,
    requested_revision: str,
) -> None:
    if provenance.get("repository_id") != repository_id:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository-intelligence provenance repository mismatch",
        )
    if provenance.get("requested_revision") != requested_revision:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository-intelligence provenance requested-revision mismatch",
        )
    resolved = provenance.get("resolved_revision")
    if not isinstance(resolved, str) or len(resolved) not in {40, 64}:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository-intelligence provenance lacks immutable resolved revision",
        )
    try:
        int(resolved, 16)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository-intelligence resolved revision is not hexadecimal",
        ) from exc


def _render_slice(output: dict[str, JsonValue]) -> str:
    path = output.get("path")
    lines = output.get("lines")
    if not isinstance(path, str) or not isinstance(lines, list):
        raise ContractError(
            ErrorCode.INVALID_PROVIDER_RESPONSE,
            "repository source-slice output is malformed",
        )
    rendered: list[str] = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        number = item.get("line")
        text = item.get("text")
        if isinstance(number, int) and not isinstance(number, bool) and isinstance(text, str):
            rendered.append(f"{number}: {text}")
    if not rendered:
        return ""
    return f"{path}\n" + "\n".join(rendered)


def _context_freshness(value: JsonValue) -> ContextFreshness:
    if isinstance(value, str) and value in _CURRENT_FRESHNESS:
        return ContextFreshness.CURRENT
    if value == "stale_index":
        return ContextFreshness.STALE
    if value == "unknown":
        return ContextFreshness.UNKNOWN
    return ContextFreshness.UNKNOWN


__all__ = [
    "RepositoryContextCaller",
    "RepositoryContextFunnel",
    "RepositoryContextFunnelReport",
    "RepositoryContextRequest",
    "RepositoryContextRequestResolver",
    "RepositoryIntelligenceContextSourceAdapter",
]
