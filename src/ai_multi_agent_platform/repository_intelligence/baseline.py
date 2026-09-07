"""Deterministic read-only repository-intelligence baseline for issue #502."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityToolProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    HealthStatus,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.repositories import RepositoryTree

from .capabilities import (
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)
from .models import (
    RepositoryIntelligenceFreshness,
    RepositoryIntelligenceProvenance,
)

RepositorySnapshotLoader = Callable[
    [str, str, OperationContext],
    Awaitable[RepositoryTree],
]

_MAX_SOURCE_SLICE_LINES = 500
_MAX_LINE_PREVIEW_CHARS = 4096


class BaselineRepositoryIntelligenceProvider(CapabilityToolProvider):
    """Read exact repository snapshots without owning repository/workspace lifecycle.

    ``snapshot_loader`` is deliberately injected. Production composition must supply a loader
    that crosses the canonical #82/#37 authorization/materialization boundary; this provider
    never reaches into LocalGitRepositoryProvider paths or provider-private worktrees.
    """

    def __init__(
        self,
        snapshot_loader: RepositorySnapshotLoader,
        *,
        provider_id: str = "platform.repository-intelligence.baseline",
        priority: int = 0,
        health: HealthStatus = HealthStatus.HEALTHY,
        available: bool = True,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("repository intelligence provider_id must not be blank")
        self._snapshot_loader = snapshot_loader
        self._provider_id = provider_id
        self._priority = priority
        self._health = health
        self._available = available

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="repository_intelligence",
            supported_operations=("discover", "invoke"),
            capabilities=tuple(
                Capability(
                    name=operation.value,
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                )
                for operation in RepositoryIntelligenceOperation
            ),
            health=self._health,
            available=self._available,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        specs = {spec.capability_id: spec for spec in repository_intelligence_capability_specs()}
        return tuple(
            CapabilityRegistration(
                capability=specs[operation.value],
                provider_id=self._provider_id,
                provider_tool_ref=operation.value,
                priority=self._priority,
            )
            for operation in RepositoryIntelligenceOperation
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            operation = RepositoryIntelligenceOperation(invocation.tool_ref)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository-intelligence provider does not expose {invocation.tool_ref!r}",
                provider_id=self._provider_id,
            ) from exc

        arguments = invocation.arguments_json()
        if operation is RepositoryIntelligenceOperation.HEALTH:
            return self._result(
                invocation,
                {
                    "provider_id": self._provider_id,
                    "health": self._health.value,
                    "available": self._available,
                },
            )
        if operation is RepositoryIntelligenceOperation.INDEX_STATUS:
            return self._result(
                invocation,
                {
                    "provider_id": self._provider_id,
                    "indexed": False,
                    "state_class": "derived_index",
                    "freshness": RepositoryIntelligenceFreshness.LIVE_REVISION.value,
                    "rebuild_required": False,
                    "notes": (
                        "baseline reads exact repository snapshots and owns no persistent index"
                    ),
                },
            )

        repository_id = _required_string(arguments, "repository_id")
        revision = _optional_string(arguments, "revision") or "HEAD"
        tree = await self._snapshot_loader(repository_id, revision, invocation.context)
        provenance = RepositoryIntelligenceProvenance(
            repository_id=tree.repository_id,
            requested_revision=tree.requested_ref,
            resolved_revision=tree.resolved_revision,
            intelligence_provider_id=self._provider_id,
            freshness=RepositoryIntelligenceFreshness.LIVE_REVISION,
        )

        if operation is RepositoryIntelligenceOperation.MAP:
            output = self._map(tree, arguments)
        elif operation is RepositoryIntelligenceOperation.TEXT_SEARCH:
            output = self._text_search(tree, arguments)
        elif operation is RepositoryIntelligenceOperation.SOURCE_SLICE:
            output = self._source_slice(tree, arguments)
        else:  # pragma: no cover - exhaustive enum guard
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                f"repository-intelligence operation is not implemented: {operation.value}",
                provider_id=self._provider_id,
            )
        output["provenance"] = provenance.to_dict()
        return self._result(invocation, output)

    def _map(
        self,
        tree: RepositoryTree,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        prefix = _optional_string(arguments, "path_prefix")
        max_entries = _bounded_int(arguments, "max_entries", default=1000, maximum=5000)
        entries: list[JsonValue] = [
            {"path": entry.relative_path, "size_bytes": len(entry.data)}
            for entry in sorted(tree.entries, key=lambda item: item.relative_path)
            if prefix is None or entry.relative_path.startswith(prefix)
        ]
        limited = entries[:max_entries]
        return {
            "entries": limited,
            "returned_entries": len(limited),
            "total_matching_entries": len(entries),
            "truncated": len(limited) < len(entries),
        }

    def _text_search(
        self,
        tree: RepositoryTree,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        query = _required_string(arguments, "query")
        prefix = _optional_string(arguments, "path_prefix")
        case_sensitive = _optional_bool(arguments, "case_sensitive", default=False)
        max_results = _bounded_int(arguments, "max_results", default=50, maximum=500)
        needle = query if case_sensitive else query.casefold()

        hits: list[JsonValue] = []
        skipped_binary_files = 0
        for entry in sorted(tree.entries, key=lambda item: item.relative_path):
            if prefix is not None and not entry.relative_path.startswith(prefix):
                continue
            try:
                text = entry.data.decode("utf-8")
            except UnicodeDecodeError:
                skipped_binary_files += 1
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                preview = line[:_MAX_LINE_PREVIEW_CHARS]
                hits.append(
                    {
                        "path": entry.relative_path,
                        "line": line_number,
                        "text": preview,
                        "preview_truncated": len(preview) < len(line),
                    }
                )
                if len(hits) >= max_results:
                    return {
                        "query": query,
                        "hits": hits,
                        "truncated": True,
                        "skipped_binary_files": skipped_binary_files,
                    }
        return {
            "query": query,
            "hits": hits,
            "truncated": False,
            "skipped_binary_files": skipped_binary_files,
        }

    def _source_slice(
        self,
        tree: RepositoryTree,
        arguments: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        path = _required_string(arguments, "path")
        start_line = _bounded_int(
            arguments,
            "start_line",
            default=1,
            maximum=2_147_483_647,
        )
        end_line = _optional_int(arguments, "end_line")
        if end_line is None:
            end_line = start_line + 199
        if end_line < start_line:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "repository source slice end_line must not precede start_line",
            )
        if end_line - start_line + 1 > _MAX_SOURCE_SLICE_LINES:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"repository source slice is limited to {_MAX_SOURCE_SLICE_LINES} lines",
            )

        entry = next((item for item in tree.entries if item.relative_path == path), None)
        if entry is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"repository source path not found at requested revision: {path}",
            )
        try:
            text = entry.data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"repository source path is not UTF-8 text: {path}",
            ) from exc

        lines = text.splitlines()
        actual_end = min(end_line, len(lines))
        selected = lines[start_line - 1 : actual_end] if start_line <= len(lines) else []
        return {
            "path": path,
            "start_line": start_line,
            "end_line": actual_end,
            "requested_end_line": end_line,
            "total_lines": len(lines),
            "lines": [
                {"line": number, "text": value}
                for number, value in enumerate(selected, start=start_line)
            ],
        }

    def _result(
        self,
        invocation: ToolInvocation,
        output: dict[str, JsonValue],
    ) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output=output)


def _required_string(arguments: dict[str, JsonValue], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository-intelligence argument {key!r} must be a non-blank string",
        )
    return value


def _optional_string(arguments: dict[str, JsonValue], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository-intelligence argument {key!r} must be a non-blank string",
        )
    return value


def _optional_bool(arguments: dict[str, JsonValue], key: str, *, default: bool) -> bool:
    value = arguments.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository-intelligence argument {key!r} must be boolean",
        )
    return value


def _optional_int(arguments: dict[str, JsonValue], key: str) -> int | None:
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository-intelligence argument {key!r} must be an integer",
        )
    return value


def _bounded_int(
    arguments: dict[str, JsonValue],
    key: str,
    *,
    default: int,
    maximum: int,
) -> int:
    value = _optional_int(arguments, key)
    if value is None:
        value = default
    if value < 1 or value > maximum:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"repository-intelligence argument {key!r} must be between 1 and {maximum}",
        )
    return value
