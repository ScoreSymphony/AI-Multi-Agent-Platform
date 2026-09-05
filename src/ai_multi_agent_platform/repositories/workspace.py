"""Repository-to-Workspace materialization through canonical FileProvider objects."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.workspaces import (
    ResolvedWorkspaceSource,
    WorkspaceFile,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceSourceResolver,
)

from .service import RepositoryRegistry


class RepositoryWorkspaceSourceResolver(WorkspaceSourceResolver):
    """Resolve one canonical repository reference at an exact Git revision into #13 Files."""

    def __init__(self, repositories: RepositoryRegistry, files: FileProvider) -> None:
        self._repositories = repositories
        self._files = files

    @property
    def kind(self) -> WorkspaceSourceKind:
        return WorkspaceSourceKind.REPOSITORY

    async def resolve(
        self,
        source_ref: WorkspaceSourceRef,
        context: DataAccessContext,
    ) -> ResolvedWorkspaceSource:
        if source_ref.kind is not self.kind:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "repository resolver received another workspace source kind",
            )
        binding = self._repositories.resolve(source_ref.ref)
        project_id = binding.connection.connection.project_id
        if project_id is not None and context.project_id != project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository connection belongs to another project",
            )
        requested_revision = (
            source_ref.revision
            or binding.reference.target_revision
            or binding.reference.default_branch
            or "HEAD"
        )
        tree = await binding.provider.read_tree(
            binding.reference,
            requested_revision,
            context.operation,
        )
        workspace_files: list[WorkspaceFile] = []
        for entry in tree.entries:
            record = await self._files.create_file(
                entry.data,
                context,
                metadata={
                    "source_kind": "repository",
                    "repository_id": binding.reference.id,
                    "repository_revision": tree.resolved_revision,
                    "repository_path": entry.relative_path,
                    "repository_provider": binding.provider.provider_id,
                },
            )
            workspace_files.append(
                WorkspaceFile(
                    relative_path=entry.relative_path,
                    file_id=record.file_id,
                    sha256=record.sha256,
                )
            )
        resolved_ref = WorkspaceSourceRef(
            kind=WorkspaceSourceKind.REPOSITORY,
            ref=binding.reference.id,
            revision=tree.resolved_revision,
            metadata={
                **source_ref.metadata,
                "requested_revision": requested_revision,
                "provider_id": binding.provider.provider_id,
            },
        )
        return ResolvedWorkspaceSource(
            source_ref=resolved_ref,
            files=tuple(workspace_files),
        )
