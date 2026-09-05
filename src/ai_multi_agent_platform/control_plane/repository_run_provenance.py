"""Optional Repository provenance hook for immutable Run Workspace bindings."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.repositories.run_integration import RepositoryRunIntegration
from ai_multi_agent_platform.workspaces import RunWorkspaceBinding, Workspace, WorkspaceSnapshot

from .models import RequestContext
from .workspace_contract import _data_access_context

_repository_request_context: ContextVar[RequestContext | None] = ContextVar(
    "repository_run_request_context",
    default=None,
)


class RepositoryRunProvenanceMixin:
    """Record exact repository inputs after Run binding and before execution dispatch.

    The mixin is inert until ``configure_repository_run_integration`` is called. It preserves
    the canonical Workspace/Run contracts and uses the existing immutable RunWorkspaceBinding
    boundary rather than adding repository-specific fields to those domain models.
    """

    _repository_run_integration: RepositoryRunIntegration | None = None

    @property
    def repository_run_integration(self) -> RepositoryRunIntegration | None:
        return self._repository_run_integration

    def configure_repository_run_integration(
        self,
        integration: RepositoryRunIntegration | None,
    ) -> None:
        """Attach or remove the optional repository Run provenance integration."""

        self._repository_run_integration = integration

    async def start_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if self._repository_run_integration is None:
            return cast(
                dict[str, JsonValue],
                await super().start_task(context, task_id, payload),  # type: ignore[misc]
            )
        token = _repository_request_context.set(context)
        try:
            return cast(
                dict[str, JsonValue],
                await super().start_task(context, task_id, payload),  # type: ignore[misc]
            )
        finally:
            _repository_request_context.reset(token)

    async def retry_task(
        self,
        context: RequestContext,
        task_id: str,
        payload: dict[str, JsonValue] | None = None,
    ) -> dict[str, JsonValue]:
        if self._repository_run_integration is None:
            return cast(
                dict[str, JsonValue],
                await super().retry_task(context, task_id, payload),  # type: ignore[misc]
            )
        token = _repository_request_context.set(context)
        try:
            return cast(
                dict[str, JsonValue],
                await super().retry_task(context, task_id, payload),  # type: ignore[misc]
            )
        finally:
            _repository_request_context.reset(token)

    async def _bind_run(
        self,
        run_id: str,
        task_id: str,
        workspace: Workspace,
        snapshot: WorkspaceSnapshot,
    ) -> RunWorkspaceBinding:
        binding = cast(
            RunWorkspaceBinding,
            await super()._bind_run(  # type: ignore[misc]
                run_id,
                task_id,
                workspace,
                snapshot,
            ),
        )
        await self._record_repository_input(
            run_id=run_id,
            task_id=task_id,
            project_id=workspace.project_id,
            snapshot=snapshot,
        )
        return binding

    async def _bind_existing_target(
        self,
        run_id: str,
        task_id: str,
        previous: RunWorkspaceBinding,
    ) -> RunWorkspaceBinding:
        binding = cast(
            RunWorkspaceBinding,
            await super()._bind_existing_target(  # type: ignore[misc]
                run_id,
                task_id,
                previous,
            ),
        )
        integration = self._repository_run_integration
        if integration is None:
            return binding

        control_plane = cast(Any, self)
        provider = control_plane.workspace_provider
        if provider is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical WorkspaceProvider is required for repository Run provenance",
                retryable=True,
            )
        workspace = await provider.get_workspace(binding.workspace_id)
        snapshot = await provider.get_snapshot(binding.workspace_snapshot_id)
        if snapshot.workspace_id != binding.workspace_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Run workspace binding snapshot belongs to another workspace",
            )
        if snapshot.content_checksum != binding.content_checksum:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Run workspace binding checksum disagrees with the immutable snapshot",
            )
        await self._record_repository_input(
            run_id=run_id,
            task_id=task_id,
            project_id=workspace.project_id,
            snapshot=snapshot,
        )
        return binding

    async def _record_repository_input(
        self,
        *,
        run_id: str,
        task_id: str,
        project_id: str,
        snapshot: WorkspaceSnapshot,
    ) -> None:
        integration = self._repository_run_integration
        if integration is None:
            return
        context = _repository_request_context.get()
        if context is None:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "repository Run provenance requires the originating Control Plane request context",
            )
        control_plane = cast(Any, self)
        project = control_plane._scopes.get_project(project_id)
        await integration.record_input_snapshot(
            run_id=run_id,
            task_id=task_id,
            snapshot=snapshot,
            actor_ref=context.actor.principal_ref,
            context=_data_access_context(context, project),
        )
