"""Provider-neutral workspace source resolution registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext

from .contracts import WorkspaceProvider
from .models import WorkspaceFile, WorkspaceSourceKind, WorkspaceSourceRef


@dataclass(frozen=True, slots=True)
class ResolvedWorkspaceSource:
    source_ref: WorkspaceSourceRef
    files: tuple[WorkspaceFile, ...] = ()

    def __post_init__(self) -> None:
        paths = [entry.relative_path for entry in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("resolved workspace source paths must be unique")


class WorkspaceSourceResolver(ABC):
    """Resolve one provider-neutral source reference into canonical File references."""

    @property
    @abstractmethod
    def kind(self) -> WorkspaceSourceKind: ...

    @abstractmethod
    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource: ...


class WorkspaceSourceResolverRegistry:
    """Explicit resolver registry; source-control connectors register here later."""

    def __init__(self, resolvers: tuple[WorkspaceSourceResolver, ...] = ()) -> None:
        self._resolvers: dict[WorkspaceSourceKind, WorkspaceSourceResolver] = {}
        for resolver in resolvers:
            self.register(resolver)

    def register(self, resolver: WorkspaceSourceResolver) -> None:
        if resolver.kind in self._resolvers:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"workspace source resolver already registered: {resolver.kind.value}",
            )
        self._resolvers[resolver.kind] = resolver

    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource:
        resolver = self._resolvers.get(source_ref.kind)
        if resolver is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"workspace source resolver is not configured: {source_ref.kind.value}",
                retryable=True,
                details={"source_kind": source_ref.kind.value},
            )
        resolved = await resolver.resolve(source_ref, context)
        if resolved.source_ref.kind is not source_ref.kind:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workspace source resolver changed the canonical source kind",
            )
        return resolved

    async def resolve_all(
        self,
        source_refs: tuple[WorkspaceSourceRef, ...],
        context: DataAccessContext,
    ) -> tuple[ResolvedWorkspaceSource, ...]:
        resolved_items: list[ResolvedWorkspaceSource] = []
        for source_ref in source_refs:
            resolved_items.append(await self.resolve(source_ref, context))
        resolved = tuple(resolved_items)
        paths: set[str] = set()
        for source in resolved:
            for entry in source.files:
                if entry.relative_path in paths:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        f"workspace sources overlap at path: {entry.relative_path}",
                    )
                paths.add(entry.relative_path)
        return resolved


class EmptyWorkspaceSourceResolver(WorkspaceSourceResolver):
    @property
    def kind(self) -> WorkspaceSourceKind:
        return WorkspaceSourceKind.EMPTY

    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource:
        del context
        if source_ref.kind is not self.kind:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "empty resolver received another source kind",
            )
        return ResolvedWorkspaceSource(source_ref=source_ref)


class SnapshotWorkspaceSourceResolver(WorkspaceSourceResolver):
    def __init__(self, workspaces: WorkspaceProvider) -> None:
        self._workspaces = workspaces

    @property
    def kind(self) -> WorkspaceSourceKind:
        return WorkspaceSourceKind.SNAPSHOT

    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource:
        if source_ref.kind is not self.kind:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "snapshot resolver received another source kind",
            )
        snapshot = await self._workspaces.get_snapshot(source_ref.ref)
        source_workspace = await self._workspaces.get_workspace(snapshot.workspace_id)
        if context.project_id is None or source_workspace.project_id != context.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "workspace source snapshot belongs to another project",
            )
        if source_ref.checksum is not None and source_ref.checksum != snapshot.content_checksum:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workspace source snapshot checksum mismatch",
            )
        if source_ref.revision is not None and source_ref.revision != str(snapshot.revision):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workspace source snapshot revision mismatch",
            )
        return ResolvedWorkspaceSource(source_ref=source_ref, files=snapshot.files)
