"""Guarded compensation adapters for canonical resources created by Templates."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.automation import AutomationService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .application import ContextualTemplateHandlerRegistry, TemplateInstantiationContext
from .models import (
    TemplateInstantiationProvenance,
    TemplateResourceRef,
    TemplateType,
)


@dataclass(slots=True)
class AgentTemplateCompensator:
    service: AgentService

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del context
        for resource in reversed(resources):
            _require_type(resource, "agent")
            self.service.delete_agent(
                resource.resource_id,
                expected_owner_ref=provenance.applied_by,
            )


@dataclass(slots=True)
class AgentTeamTemplateCompensator:
    service: AgentService

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del context
        for resource in reversed(resources):
            _require_type(resource, "agent_team")
            self.service.delete_team(
                resource.resource_id,
                expected_owner_ref=provenance.applied_by,
            )


@dataclass(slots=True)
class AutomationTemplateCompensator:
    service: AutomationService

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del provenance, context
        for resource in reversed(resources):
            _require_type(resource, "automation")
            await self.service.repository.remove_automation_if_unused(resource.resource_id)


@dataclass(slots=True)
class ProjectTemplateCompensator:
    scopes: ScopeStore

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del provenance, context
        for resource in reversed(resources):
            _require_type(resource, "project")
            # Template application compensates in reverse dependency order, so resources
            # created later in this same graph have already been removed. ScopeStore still
            # performs its own Workspace guard and refuses compensation if that proof is
            # no longer true.
            self.scopes.compensate_project(
                resource.resource_id,
                external_dependencies=(),
            )


@dataclass(slots=True)
class WorkspaceStructureTemplateCompensator:
    provider: WorkspaceProvider

    async def compensate(
        self,
        resources: tuple[TemplateResourceRef, ...],
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> None:
        del provenance, context
        for resource in reversed(resources):
            _require_type(resource, "workspace")
            await self.provider.compensate_workspace(resource.resource_id)


def register_template_compensators(
    registry: ContextualTemplateHandlerRegistry,
    *,
    agents: AgentService | None = None,
    automations: AutomationService | None = None,
    scopes: ScopeStore | None = None,
    workspaces: WorkspaceProvider | None = None,
) -> None:
    """Bind compensation only to canonical services actually present in a deployment."""

    if agents is not None:
        registry.register_compensator(TemplateType.AGENT, AgentTemplateCompensator(agents))
        registry.register_compensator(
            TemplateType.AGENT_TEAM,
            AgentTeamTemplateCompensator(agents),
        )
    if automations is not None:
        registry.register_compensator(
            TemplateType.AUTOMATION,
            AutomationTemplateCompensator(automations),
        )
    if scopes is not None:
        registry.register_compensator(TemplateType.PROJECT, ProjectTemplateCompensator(scopes))
    if workspaces is not None:
        registry.register_compensator(
            TemplateType.WORKSPACE_STRUCTURE,
            WorkspaceStructureTemplateCompensator(workspaces),
        )


def _require_type(resource: TemplateResourceRef, expected: str) -> None:
    if resource.resource_type != expected:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Template compensation received an unexpected canonical resource type",
            details={
                "expected_resource_type": expected,
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
            },
        )


__all__ = [
    "AgentTeamTemplateCompensator",
    "AgentTemplateCompensator",
    "AutomationTemplateCompensator",
    "ProjectTemplateCompensator",
    "WorkspaceStructureTemplateCompensator",
    "register_template_compensators",
]
