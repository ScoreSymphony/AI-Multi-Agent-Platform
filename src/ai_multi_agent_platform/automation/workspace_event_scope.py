"""Authoritative workspace resolution for canonical Automation platform events.

Canonical Event intentionally remains free of an Automation-only workspace field.  Workspace
scope is instead proven from #37 workspace state and immutable Run workspace bindings.  Any event
whose workspace cannot be proven resolves to ``None`` so workspace-scoped Automations fail closed.
"""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.types import PlatformEvent
from ai_multi_agent_platform.workspaces import RunWorkspaceBindingRepository, WorkspaceProvider


class WorkspaceEventScopeResolver(Protocol):
    """Resolve one canonical Event to a canonical workspace ID, or fail closed with ``None``."""

    async def resolve_workspace_id(self, event: PlatformEvent) -> str | None: ...


class CanonicalWorkspaceEventScopeResolver:
    """Resolve Event workspace scope only from canonical #37 workspace relationships.

    Durable Run bindings are authoritative for Run subjects, including after a local
    materialization has been released.  Active Workspace task/run references provide the
    reference-path fallback for events produced while a materialization is active.  Ambiguous or
    unresolvable relationships return ``None`` rather than broadening visibility.
    """

    def __init__(
        self,
        *,
        workspace_provider: WorkspaceProvider | None = None,
        run_workspace_bindings: RunWorkspaceBindingRepository | None = None,
    ) -> None:
        self._workspace_provider = workspace_provider
        self._run_workspace_bindings = run_workspace_bindings

    async def resolve_workspace_id(self, event: PlatformEvent) -> str | None:
        if event.subject_type == "run" and self._run_workspace_bindings is not None:
            binding = await self._run_workspace_bindings.get(event.subject_id)
            if binding is not None:
                return await self._validated_workspace_id(binding.workspace_id, event)

        if self._workspace_provider is None:
            return None

        candidates = await self._workspace_provider.list_workspaces(project_id=event.project_id)
        matches: list[str] = []
        for workspace in candidates:
            if event.owner_ref is not None and workspace.owner_ref != event.owner_ref:
                continue
            if event.subject_type == "task" and event.subject_id in workspace.active_task_ids:
                matches.append(workspace.id)
            elif event.subject_type == "run" and event.subject_id in workspace.active_run_ids:
                matches.append(workspace.id)

        if len(matches) != 1:
            return None
        return matches[0]

    async def _validated_workspace_id(self, workspace_id: str, event: PlatformEvent) -> str | None:
        if self._workspace_provider is None:
            return workspace_id
        workspace = await self._workspace_provider.get_workspace(workspace_id)
        if event.project_id is not None and workspace.project_id != event.project_id:
            return None
        if event.owner_ref is not None and workspace.owner_ref != event.owner_ref:
            return None
        return workspace.id
