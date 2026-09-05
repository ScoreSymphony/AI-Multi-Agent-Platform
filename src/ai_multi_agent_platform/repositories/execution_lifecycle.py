"""Repository-backed Workspace materialization hooks for the canonical execution lifecycle."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts import ExecutionRequest as KernelExecutionRequest
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.execution.contracts import ExecutionResult, ExecutionStatus
from ai_multi_agent_platform.workspaces import (
    MaterializationOutcome,
    RunWorkspaceBinding,
    RunWorkspaceBindingRepository,
    WorkspaceMaterialization,
    WorkspaceProvider,
    WorkspaceSourceKind,
)

from .run_integration import RepositoryRunIntegration
from .service import RepositoryProvenanceStore


class RepositoryWorkspaceExecutionCoordinator:
    """Materialize exact Run bindings and capture repository-backed outputs at completion.

    The coordinator is intentionally split from ``ExecutorLifecycleBackend``. The execution
    layer only knows how to ask for an opaque workspace token and report terminal results;
    this repository/workspace bridge owns the #82-specific provenance and artifact behavior.
    """

    def __init__(
        self,
        bindings: RunWorkspaceBindingRepository,
        workspaces: WorkspaceProvider,
        provenance: RepositoryProvenanceStore,
        *,
        fallback_workspace: str,
    ) -> None:
        if not fallback_workspace.strip():
            raise ValueError("fallback execution workspace must not be blank")
        self._bindings = bindings
        self._workspaces = workspaces
        self._provenance = provenance
        self._fallback_workspace = fallback_workspace
        self._run_integration: RepositoryRunIntegration | None = None
        self._materializations: dict[str, WorkspaceMaterialization] = {}
        self._completed_runs: set[str] = set()

    def configure_run_integration(self, integration: RepositoryRunIntegration | None) -> None:
        """Late-bind the artifact/provenance service after the kernel has been composed."""

        self._run_integration = integration

    async def resolve_execution_workspace(self, request: KernelExecutionRequest) -> str:
        """Return the opaque executor token for the exact WorkspaceSnapshot bound to a Run."""

        binding = await self._bindings.get(request.run_id)
        if binding is None:
            return self._fallback_workspace

        existing = self._materializations.get(request.run_id)
        if existing is not None:
            self._validate_materialization(existing, binding)
            return existing.execution_workspace

        workspace = await self._workspaces.get_workspace(binding.workspace_id)
        snapshot = await self._workspaces.get_snapshot(binding.workspace_snapshot_id)
        if snapshot.workspace_id != binding.workspace_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Run workspace binding snapshot belongs to another workspace",
                details={"run_id": request.run_id},
            )
        if snapshot.content_checksum != binding.content_checksum:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Run workspace binding checksum disagrees with the immutable snapshot",
                details={"run_id": request.run_id},
            )
        if request.context.project_id != workspace.project_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Run execution context project disagrees with the bound Workspace",
                details={"run_id": request.run_id},
            )

        materialization = await self._workspaces.materialize(
            binding.workspace_id,
            self._execution_data_context(request, binding, actor_ref="service:platform-execution"),
            snapshot_id=binding.workspace_snapshot_id,
            task_id=binding.task_id,
            run_id=request.run_id,
        )
        self._validate_materialization(materialization, binding)
        self._materializations[request.run_id] = materialization
        return materialization.execution_workspace

    async def observe_terminal_result(
        self,
        request: KernelExecutionRequest,
        result: ExecutionResult,
    ) -> None:
        """Capture changed canonical files/artifacts, then release the bounded materialization."""

        if request.run_id in self._completed_runs:
            return
        materialization = self._materializations.get(request.run_id)
        if materialization is None:
            return
        binding = await self._require_binding(request.run_id)
        self._validate_materialization(materialization, binding)
        snapshot = await self._workspaces.get_snapshot(binding.workspace_snapshot_id)
        repository_backed = any(
            source.kind is WorkspaceSourceKind.REPOSITORY for source in snapshot.source_refs
        )
        records = self._provenance.for_run(request.run_id)

        if repository_backed and not records:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository-backed Run reached completion without input revision provenance",
                details={"run_id": request.run_id},
            )

        if records:
            integration = self._run_integration
            if integration is None:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    "repository Run integration is not configured for completion capture",
                    retryable=True,
                )
            actor_refs = {record.actor_ref for record in records}
            if len(actor_refs) != 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository Run provenance contains inconsistent actors",
                    details={"run_id": request.run_id},
                )
            agent_ids = {record.agent_id for record in records if record.agent_id is not None}
            if len(agent_ids) > 1:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "repository Run provenance contains inconsistent agents",
                    details={"run_id": request.run_id},
                )
            actor_ref = next(iter(actor_refs))
            agent_id = next(iter(agent_ids)) if agent_ids else None
            await integration.capture_workspace_changes(
                run_id=request.run_id,
                task_id=binding.task_id,
                materialization_id=materialization.id,
                actor_ref=actor_ref,
                agent_id=agent_id,
                context=self._execution_data_context(request, binding, actor_ref=actor_ref),
            )

        await self._workspaces.release_materialization(
            materialization.id,
            self._materialization_outcome(result.status),
        )
        self._materializations.pop(request.run_id, None)
        self._completed_runs.add(request.run_id)

    async def _require_binding(self, run_id: str) -> RunWorkspaceBinding:
        binding = await self._bindings.get(run_id)
        if binding is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "materialized Run no longer has its immutable Workspace binding",
                details={"run_id": run_id},
            )
        return binding

    @staticmethod
    def _validate_materialization(
        materialization: WorkspaceMaterialization,
        binding: RunWorkspaceBinding,
    ) -> None:
        if (
            materialization.workspace_id != binding.workspace_id
            or materialization.snapshot_id != binding.workspace_snapshot_id
            or materialization.run_id != binding.run_id
            or materialization.task_id != binding.task_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Workspace materialization disagrees with the immutable Run binding",
                details={"run_id": binding.run_id},
            )

    @staticmethod
    def _execution_data_context(
        request: KernelExecutionRequest,
        binding: RunWorkspaceBinding,
        *,
        actor_ref: str,
    ) -> DataAccessContext:
        return DataAccessContext(
            operation=request.context,
            actor_ref=actor_ref,
            task_id=binding.task_id,
            run_id=binding.run_id,
            audit_metadata={"source": "repository-workspace-execution"},
        )

    @staticmethod
    def _materialization_outcome(status: ExecutionStatus) -> MaterializationOutcome:
        if status is ExecutionStatus.SUCCEEDED:
            return MaterializationOutcome.SUCCEEDED
        if status is ExecutionStatus.CANCELLED:
            return MaterializationOutcome.CANCELLED
        return MaterializationOutcome.FAILED


__all__ = ["RepositoryWorkspaceExecutionCoordinator"]
