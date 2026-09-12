"""#37/#82 composition for isolated coding-workstream materialization."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.repositories import RepositoryCallContext, RepositoryService
from ai_multi_agent_platform.workspaces import (
    WorkspaceAccessMode,
    WorkspaceProvider,
    WorkspaceRetention,
    WorkspaceSourceKind,
    WorkspaceSourceRef,
    WorkspaceType,
)

from .models import CodingWorkstream, WorkstreamState
from .service import CodingBatchCoordinator, deterministic_branch_ref


def deterministic_workspace_id(batch_id: str, workstream_id: str) -> str:
    """Create a replay-stable canonical Workspace ID for one logical workstream."""

    value = uuid5(NAMESPACE_URL, f"ai-multi-agent-platform:{batch_id}:{workstream_id}")
    return f"workspace_{value}"


class CanonicalWorkstreamMaterializer:
    """Allocate isolated #37 Workspace state and provider-local #82 branch metadata.

    Agent/AgentRun allocation remains a #33/#384 responsibility.  This adapter receives the exact
    revision/run identity from that authority and binds it to the Workspace/repository evidence.
    """

    def __init__(
        self,
        coordinator: CodingBatchCoordinator,
        workspaces: WorkspaceProvider,
        repositories: RepositoryService,
    ) -> None:
        self._coordinator = coordinator
        self._workspaces = workspaces
        self._repositories = repositories

    async def ensure_materialized(
        self,
        batch_id: str,
        workstream_id: str,
        *,
        project_id: str,
        owner_ref: OwnerRef,
        data_context: DataAccessContext,
        repository_context: RepositoryCallContext,
        agent_revision: str,
        agent_run_id: str,
    ) -> CodingWorkstream:
        batch = self._coordinator.get(batch_id)
        workstream = batch.workstream(workstream_id)
        workspace_id = deterministic_workspace_id(batch.batch_id, workstream_id)
        branch_ref = deterministic_branch_ref(batch.batch_id, workstream_id)

        # A restart may replay materialization after execution has already advanced the branch.
        # Reconcile the canonical evidence first instead of incorrectly requiring the provider
        # branch to still point at the original base revision.
        if workstream.provenance.workspace_id is not None:
            expected = (
                workspace_id,
                workstream.provenance.snapshot_id,
                agent_revision,
                agent_run_id,
                branch_ref,
            )
            recorded = (
                workstream.provenance.workspace_id,
                workstream.provenance.snapshot_id,
                workstream.provenance.agent_revision,
                workstream.provenance.agent_run_id,
                workstream.provenance.branch_ref,
            )
            if recorded != expected:
                raise ValueError(
                    "materialization retry conflicts with recorded canonical provenance"
                )
            return workstream

        if workstream.state is not WorkstreamState.READY:
            raise ValueError("only ready coding workstreams may be materialized")

        existing = {
            workspace.id: workspace
            for workspace in await self._workspaces.list_workspaces(project_id=project_id)
        }
        workspace = existing.get(workspace_id)
        if workspace is None:
            workspace = await self._workspaces.create_workspace(
                project_id=project_id,
                owner_ref=owner_ref,
                workspace_type=WorkspaceType.ISOLATED_RUN,
                context=data_context,
                access_mode=WorkspaceAccessMode.READ_WRITE,
                retention=WorkspaceRetention.EPHEMERAL,
                source_refs=(
                    WorkspaceSourceRef(
                        kind=WorkspaceSourceKind.REPOSITORY,
                        ref=batch.repository_id,
                        revision=batch.base_revision,
                        metadata={"coding_batch_id": batch.batch_id, "workstream_id": workstream_id},
                    ),
                ),
                workspace_id=workspace_id,
            )
        source_matches = any(
            source.kind is WorkspaceSourceKind.REPOSITORY
            and source.ref == batch.repository_id
            and source.revision == batch.base_revision
            for source in workspace.source_refs
        )
        if not source_matches:
            raise ValueError("recovered Workspace does not match the coding workstream base revision")

        if workspace.base_snapshot_id is not None:
            snapshot = await self._workspaces.get_snapshot(workspace.base_snapshot_id)
        else:
            snapshot = await self._workspaces.create_snapshot(workspace.id)

        branches = await self._repositories.branches(batch.repository_id, repository_context)
        if branch_ref not in branches:
            created = await self._repositories.create_branch(
                batch.repository_id,
                branch_ref,
                repository_context,
                start_revision=batch.base_revision,
                checkout=False,
            )
            if created.commit_sha != batch.base_revision:
                raise ValueError("repository authority created workstream branch from another revision")
        else:
            commits = await self._repositories.commits(
                batch.repository_id,
                repository_context,
                revision=branch_ref,
                limit=1,
            )
            if not commits or commits[0].revision != batch.base_revision:
                raise ValueError("existing workstream branch no longer points at the expected base")

        return self._coordinator.materialize_workstream(
            batch_id,
            workstream_id,
            workspace_id=workspace.id,
            snapshot_id=snapshot.id,
            agent_revision=agent_revision,
            agent_run_id=agent_run_id,
            branch_ref=branch_ref,
        )

    async def current_target_revision(
        self,
        batch_id: str,
        *,
        repository_context: RepositoryCallContext,
    ) -> str:
        """Resolve the current target through #82 before creating an integration candidate."""

        batch = self._coordinator.get(batch_id)
        commits = await self._repositories.commits(
            batch.repository_id,
            repository_context,
            revision=batch.target_ref,
            limit=1,
        )
        if not commits:
            raise ValueError("repository target ref has no resolvable commit")
        return commits[0].revision
