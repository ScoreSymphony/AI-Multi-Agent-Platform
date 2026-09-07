"""Production wiring for repository intelligence through canonical platform policy."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext, ToolInvocation
from ai_multi_agent_platform.data import DataAccessContext, FileProvider
from ai_multi_agent_platform.repositories import (
    RepositoryCallContext,
    RepositoryService,
    RepositoryTree,
    RepositoryTreeEntry,
)
from ai_multi_agent_platform.repositories.execution_lifecycle import (
    RepositoryWorkspaceExecutionCoordinator,
)
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from ai_multi_agent_platform.workspaces import (
    RunWorkspaceBindingRepository,
    WorkspaceFile,
    WorkspaceProvider,
    WorkspaceSourceKind,
)

from .models import RepositoryIntelligenceFreshness
from .workspace import WorkspaceRepositorySnapshot

RepositoryIntelligenceActorResolver = Callable[[OperationContext], str]

_DEFAULT_MAX_TREE_ENTRIES = 5_000
_DEFAULT_MAX_TREE_BYTES = 64 * 1024 * 1024


@runtime_checkable
class LocalMaterializationPathProvider(Protocol):
    """Refined local #37 seam used only at the single-node composition boundary."""

    def local_path(self, materialization_id: str) -> Path: ...


class AuthorizedRepositorySnapshotLoader:
    """Load exact trees through #82/#15 with pre-materialization resource ceilings."""

    def __init__(
        self,
        repositories: RepositoryService,
        *,
        actor_resolver: RepositoryIntelligenceActorResolver,
        max_entries: int = _DEFAULT_MAX_TREE_ENTRIES,
        max_total_bytes: int = _DEFAULT_MAX_TREE_BYTES,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1:
            raise ValueError("repository intelligence tree limits must be positive")
        self._repositories = repositories
        self._actor_resolver = actor_resolver
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes

    async def __call__(
        self,
        repository_id: str,
        revision: str,
        context: OperationContext,
    ) -> RepositoryTree:
        call_context = RepositoryCallContext(
            operation=context,
            actor_ref=self._actor_resolver(context),
        )
        return await self._repositories.read_tree(
            repository_id,
            revision,
            call_context,
            max_entries=self._max_entries,
            max_total_bytes=self._max_total_bytes,
        )


class AuthorizedRunWorkspaceSnapshotLoader:
    """Resolve the exact repository-shaped Workspace source visible to one canonical Run.

    The immutable ``RunWorkspaceBinding`` proves Workspace/Snapshot identity. #15 authorizes
    the Workspace read before any bytes are touched. If the local execution coordinator owns an
    active materialization, its current bytes are read through a narrowly refined local path seam;
    no dirty bytes are persisted merely to answer a read-only intelligence request. If a Run is
    active but its materialization is not locally observable, the loader fails closed instead of
    silently returning the stale immutable input snapshot.
    """

    def __init__(
        self,
        bindings: RunWorkspaceBindingRepository,
        workspaces: WorkspaceProvider,
        files: FileProvider,
        execution: RepositoryWorkspaceExecutionCoordinator,
        authorization: AuthorizationGate,
        *,
        actor_resolver: RepositoryIntelligenceActorResolver,
        max_entries: int = _DEFAULT_MAX_TREE_ENTRIES,
        max_total_bytes: int = _DEFAULT_MAX_TREE_BYTES,
    ) -> None:
        if max_entries < 1 or max_total_bytes < 1:
            raise ValueError("repository intelligence workspace limits must be positive")
        self._bindings = bindings
        self._workspaces = workspaces
        self._files = files
        self._execution = execution
        self._authorization = authorization
        self._actor_resolver = actor_resolver
        self._max_entries = max_entries
        self._max_total_bytes = max_total_bytes

    async def __call__(
        self,
        repository_id: str,
        revision: str,
        invocation: ToolInvocation,
    ) -> WorkspaceRepositorySnapshot | None:
        run_id = invocation.run_id
        if run_id is None:
            return None
        binding = await self._bindings.get(run_id)
        if binding is None:
            return None
        if invocation.task_id is not None and invocation.task_id != binding.task_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository intelligence Run/Task context disagrees with Workspace binding",
                details={"run_id": run_id},
            )

        workspace = await self._workspaces.get_workspace(binding.workspace_id)
        snapshot = await self._workspaces.get_snapshot(binding.workspace_snapshot_id)
        if snapshot.workspace_id != binding.workspace_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository intelligence Workspace binding snapshot belongs to another Workspace",
                details={"run_id": run_id},
            )
        if snapshot.content_checksum != binding.content_checksum:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository intelligence Workspace binding checksum mismatch",
                details={"run_id": run_id},
            )
        if invocation.context.project_id != workspace.project_id:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "repository intelligence Workspace belongs to another project",
                details={"workspace_id": workspace.id},
            )

        repository_sources = tuple(
            source for source in snapshot.source_refs if source.kind is WorkspaceSourceKind.REPOSITORY
        )
        matching_sources = tuple(
            source for source in repository_sources if source.ref == repository_id
        )
        if not matching_sources:
            return None
        if len(repository_sources) != 1 or len(matching_sources) != 1:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "repository intelligence cannot attribute a merged multi-repository Workspace",
                details={"workspace_id": workspace.id, "repository_id": repository_id},
            )
        source = matching_sources[0]
        if source.revision is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository-backed Workspace source has no immutable revision",
                details={"workspace_id": workspace.id, "repository_id": repository_id},
            )

        requested_source_revision = source.metadata.get("requested_revision")
        compatible_revisions = {"HEAD", source.revision}
        if isinstance(requested_source_revision, str) and requested_source_revision.strip():
            compatible_revisions.add(requested_source_revision)
        if revision not in compatible_revisions:
            # The caller deliberately asked for another revision. Let the ordinary #82 path
            # resolve that exact repository state instead of substituting the Run Workspace.
            return None

        actor_ref = self._actor_resolver(invocation.context)
        await self._authorization.enforce(
            ProposedAction(
                AuthorizationContext(
                    actor=infer_actor_identity(actor_ref),
                    action=AuthorizationAction.READ,
                    resource_type=ResourceType.WORKSPACE,
                    resource_id=workspace.id,
                    operation=invocation.context,
                    workspace_id=workspace.id,
                    task_id=invocation.task_id,
                    run_id=run_id,
                    agent_id=invocation.agent_id,
                    capability_ref=invocation.tool_ref,
                    side_effect="none",
                ),
                payload={
                    "repository_id": repository_id,
                    "workspace_snapshot_id": snapshot.id,
                    "read_mode": "repository-intelligence",
                },
            )
        )
        data_context = DataAccessContext(
            operation=invocation.context,
            actor_ref=actor_ref,
            task_id=invocation.task_id,
            run_id=run_id,
            agent_id=invocation.agent_id,
            audit_metadata={"source": "repository-intelligence-workspace"},
        )

        materialization = self._execution.active_materialization(run_id)
        if materialization is not None:
            if (
                materialization.workspace_id != binding.workspace_id
                or materialization.snapshot_id != binding.workspace_snapshot_id
                or materialization.run_id != run_id
                or materialization.task_id != binding.task_id
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository intelligence active materialization disagrees with Run binding",
                    details={"run_id": run_id},
                )
            if not isinstance(self._workspaces, LocalMaterializationPathProvider):
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "active Workspace materialization is not locally readable for baseline intelligence",
                    retryable=True,
                    details={"run_id": run_id, "workspace_id": workspace.id},
                )
            tree = self._read_live_tree(
                self._workspaces.local_path(materialization.id),
                repository_id=repository_id,
                requested_revision=revision,
                resolved_revision=source.revision,
            )
            base_content_checksum = _manifest_content_checksum(snapshot.files)
            current_content_checksum = _tree_content_checksum(tree)
            return WorkspaceRepositorySnapshot(
                tree=tree,
                workspace_id=workspace.id,
                workspace_snapshot_id=snapshot.id,
                workspace_revision=snapshot.revision,
                workspace_snapshot_checksum=snapshot.content_checksum,
                source_content_checksum=current_content_checksum,
                freshness=RepositoryIntelligenceFreshness.LIVE_WORKSPACE,
                dirty=current_content_checksum != base_content_checksum,
                materialization_id=materialization.id,
            )

        if run_id in workspace.active_run_ids:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "active Workspace materialization is not observable by repository intelligence",
                retryable=True,
                details={"run_id": run_id, "workspace_id": workspace.id},
            )

        tree = await self._read_snapshot_tree(
            snapshot.files,
            repository_id=repository_id,
            requested_revision=revision,
            resolved_revision=source.revision,
            context=data_context,
        )
        return WorkspaceRepositorySnapshot(
            tree=tree,
            workspace_id=workspace.id,
            workspace_snapshot_id=snapshot.id,
            workspace_revision=snapshot.revision,
            workspace_snapshot_checksum=snapshot.content_checksum,
            source_content_checksum=_tree_content_checksum(tree),
            freshness=RepositoryIntelligenceFreshness.WORKSPACE_SNAPSHOT,
            dirty=False,
        )

    async def _read_snapshot_tree(
        self,
        files: tuple[WorkspaceFile, ...],
        *,
        repository_id: str,
        requested_revision: str,
        resolved_revision: str,
        context: DataAccessContext,
    ) -> RepositoryTree:
        if len(files) > self._max_entries:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "repository intelligence Workspace snapshot exceeds file-count limit",
                details={"max_entries": self._max_entries, "actual_entries": len(files)},
            )
        entries: list[RepositoryTreeEntry] = []
        total_bytes = 0
        for entry in sorted(files, key=lambda item: item.relative_path):
            record = await self._files.get_file(entry.file_id, context)
            if record.sha256 != entry.sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"Workspace snapshot file checksum mismatch: {entry.relative_path}",
                )
            if total_bytes + record.size_bytes > self._max_total_bytes:
                raise ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "repository intelligence Workspace snapshot exceeds byte limit",
                    details={"max_total_bytes": self._max_total_bytes},
                )
            chunks: list[bytes] = []
            async for chunk in self._files.stream_file(entry.file_id, context):
                chunks.append(chunk)
            data = b"".join(chunks)
            if len(data) != record.size_bytes or hashlib.sha256(data).hexdigest() != entry.sha256:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"Workspace snapshot file content disagrees with manifest: {entry.relative_path}",
                )
            total_bytes += len(data)
            entries.append(RepositoryTreeEntry(entry.relative_path, data))
        return RepositoryTree(
            repository_id=repository_id,
            requested_ref=requested_revision,
            resolved_revision=resolved_revision,
            entries=tuple(entries),
        )

    def _read_live_tree(
        self,
        root: Path,
        *,
        repository_id: str,
        requested_revision: str,
        resolved_revision: str,
    ) -> RepositoryTree:
        if not root.exists() or not root.is_dir():
            raise ContractError(ErrorCode.NOT_FOUND, "Workspace materialization is missing")
        if root.is_symlink():
            raise ContractError(ErrorCode.FORBIDDEN, "Workspace materialization root is a symlink")

        paths: list[Path] = []
        for current_root, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            for name in (*dirnames, *filenames):
                if (base / name).is_symlink():
                    raise ContractError(
                        ErrorCode.FORBIDDEN,
                        "Workspace materialization contains a symlink",
                    )
            for name in filenames:
                paths.append(base / name)
        if len(paths) > self._max_entries:
            raise ContractError(
                ErrorCode.RESOURCE_EXHAUSTED,
                "repository intelligence live Workspace exceeds file-count limit",
                details={"max_entries": self._max_entries, "actual_entries": len(paths)},
            )

        entries: list[RepositoryTreeEntry] = []
        total_bytes = 0
        for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix()):
            relative_path = path.relative_to(root).as_posix()
            try:
                size = path.stat().st_size
            except OSError as exc:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    f"failed to inspect Workspace materialization file: {relative_path}",
                ) from exc
            if total_bytes + size > self._max_total_bytes:
                raise ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "repository intelligence live Workspace exceeds byte limit",
                    details={"max_total_bytes": self._max_total_bytes},
                )
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ContractError(
                    ErrorCode.BACKEND_ERROR,
                    f"failed to read Workspace materialization file: {relative_path}",
                ) from exc
            if total_bytes + len(data) > self._max_total_bytes:
                raise ContractError(
                    ErrorCode.RESOURCE_EXHAUSTED,
                    "repository intelligence live Workspace exceeds byte limit",
                    details={"max_total_bytes": self._max_total_bytes},
                )
            total_bytes += len(data)
            entries.append(RepositoryTreeEntry(relative_path, data))
        return RepositoryTree(
            repository_id=repository_id,
            requested_ref=requested_revision,
            resolved_revision=resolved_revision,
            entries=tuple(entries),
        )


def _manifest_content_checksum(files: tuple[WorkspaceFile, ...]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item.relative_path):
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(entry.sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _tree_content_checksum(tree: RepositoryTree) -> str:
    digest = hashlib.sha256()
    for entry in sorted(tree.entries, key=lambda item: item.relative_path):
        digest.update(entry.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(entry.data).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()
