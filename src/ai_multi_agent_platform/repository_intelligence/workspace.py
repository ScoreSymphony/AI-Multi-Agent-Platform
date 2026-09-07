"""Run-bound Workspace source support for repository intelligence.

The deterministic repository baseline remains useful outside execution. During a Run, however,
repository-backed code can diverge from its immutable Git input. This adapter selects an exact
#37 Workspace snapshot/materialization when the Run binding proves that it belongs to the same
repository, while preserving the Git revision as base provenance rather than pretending dirty
bytes are another commit.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, ToolInvocation, ToolResult
from ai_multi_agent_platform.domain import validate_id
from ai_multi_agent_platform.repositories import RepositoryTree
from ai_multi_agent_platform.workspaces.models import validate_sha256

from .baseline import BaselineRepositoryIntelligenceProvider, RepositorySnapshotLoader
from .capabilities import RepositoryIntelligenceOperation
from .models import RepositoryIntelligenceFreshness, RepositoryIntelligenceProvenance


@dataclass(frozen=True, slots=True)
class WorkspaceRepositorySnapshot:
    """Exact repository-shaped source view proven by a canonical Run Workspace binding."""

    tree: RepositoryTree
    workspace_id: str
    workspace_snapshot_id: str
    workspace_revision: int
    workspace_snapshot_checksum: str
    source_content_checksum: str
    freshness: RepositoryIntelligenceFreshness
    dirty: bool
    materialization_id: str | None = None

    def __post_init__(self) -> None:
        validate_id(self.workspace_id, "workspace")
        validate_id(self.workspace_snapshot_id, "workspace_snapshot")
        if self.workspace_revision < 0:
            raise ValueError("workspace repository snapshot revision must be non-negative")
        object.__setattr__(
            self,
            "workspace_snapshot_checksum",
            validate_sha256(self.workspace_snapshot_checksum),
        )
        object.__setattr__(
            self,
            "source_content_checksum",
            validate_sha256(self.source_content_checksum),
        )
        if self.materialization_id is not None:
            validate_id(self.materialization_id, "materialization")
            if self.freshness is not RepositoryIntelligenceFreshness.LIVE_WORKSPACE:
                raise ValueError("live materialization provenance requires live_workspace freshness")
        elif self.freshness is not RepositoryIntelligenceFreshness.WORKSPACE_SNAPSHOT:
            raise ValueError("immutable Workspace provenance requires workspace_snapshot freshness")
        if self.dirty and self.materialization_id is None:
            raise ValueError("dirty Workspace provenance requires a live materialization")

    def provenance_details(self) -> dict[str, JsonValue]:
        return {
            "source_kind": (
                "workspace_materialization"
                if self.materialization_id is not None
                else "workspace_snapshot"
            ),
            "workspace": {
                "workspace_id": self.workspace_id,
                "workspace_snapshot_id": self.workspace_snapshot_id,
                "workspace_revision": self.workspace_revision,
                "workspace_snapshot_checksum": self.workspace_snapshot_checksum,
                "materialization_id": self.materialization_id,
                "source_content_checksum": self.source_content_checksum,
                "dirty": self.dirty,
            },
        }


WorkspaceRepositorySnapshotLoader = Callable[
    [str, str, ToolInvocation],
    Awaitable[WorkspaceRepositorySnapshot | None],
]


class WorkspaceAwareRepositoryIntelligenceProvider(BaselineRepositoryIntelligenceProvider):
    """Baseline provider that prefers the exact Run-bound Workspace source when applicable."""

    def __init__(
        self,
        snapshot_loader: RepositorySnapshotLoader,
        workspace_snapshot_loader: WorkspaceRepositorySnapshotLoader,
        **kwargs: object,
    ) -> None:
        super().__init__(snapshot_loader, **kwargs)
        self._workspace_snapshot_loader = workspace_snapshot_loader

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        try:
            operation = RepositoryIntelligenceOperation(invocation.tool_ref)
        except ValueError:
            return await super().invoke(invocation)

        if operation in {
            RepositoryIntelligenceOperation.HEALTH,
            RepositoryIntelligenceOperation.INDEX_STATUS,
        } or invocation.run_id is None:
            return await super().invoke(invocation)

        arguments = invocation.arguments_json()
        repository_id = _required_string(arguments, "repository_id")
        revision = _optional_string(arguments, "revision") or "HEAD"
        source = await self._workspace_snapshot_loader(repository_id, revision, invocation)
        if source is None:
            return await super().invoke(invocation)
        if source.tree.repository_id != repository_id:
            raise ContractError(
                ErrorCode.INVALID_PROVIDER_RESPONSE,
                "workspace repository-intelligence snapshot repository identity mismatch",
                provider_id=self.descriptor.provider_id,
            )

        if operation is RepositoryIntelligenceOperation.MAP:
            output = self._map(source.tree, arguments)
        elif operation is RepositoryIntelligenceOperation.TEXT_SEARCH:
            output = self._text_search(source.tree, arguments)
        elif operation is RepositoryIntelligenceOperation.SOURCE_SLICE:
            output = self._source_slice(source.tree, arguments)
        else:  # pragma: no cover - exhaustive enum guard
            return await super().invoke(invocation)

        provenance = RepositoryIntelligenceProvenance(
            repository_id=source.tree.repository_id,
            requested_revision=source.tree.requested_ref,
            resolved_revision=source.tree.resolved_revision,
            intelligence_provider_id=self.descriptor.provider_id,
            freshness=source.freshness,
        ).to_dict()
        provenance.update(source.provenance_details())
        output["provenance"] = provenance
        return self._result(invocation, output)


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


__all__ = [
    "WorkspaceAwareRepositoryIntelligenceProvider",
    "WorkspaceRepositorySnapshot",
    "WorkspaceRepositorySnapshotLoader",
]
