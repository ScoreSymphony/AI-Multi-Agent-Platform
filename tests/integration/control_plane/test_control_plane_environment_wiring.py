from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.control_plane import register_template_control_plane
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateTrust,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository


@dataclass
class _AgentHandler:
    template_type = TemplateType.AGENT

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="agent", action="create"),)

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance
        return (
            TemplateResourceRef(
                resource_type="agent",
                resource_id=f"agent-created-{context.instance_id}",
            ),
        )


class _WorkspaceProvider:
    def __init__(self, workspace_id: str, owner: OwnerRef) -> None:
        self.workspace_id = workspace_id
        self.owner = owner

    async def list_workspaces(self) -> tuple[object, ...]:
        return (
            SimpleNamespace(id=self.workspace_id, owner_ref=self.owner),
            SimpleNamespace(
                id="workspace-foreign",
                owner_ref=OwnerRef(type="user", id="other-user"),
            ),
        )


class _ControlPlane:
    def __init__(self, workspace_provider: object) -> None:
        self.workspace_provider = workspace_provider
        self.commands: dict[str, object] = {}
        self.resources: dict[str, object] = {}

    def register_resource_service(self, name: str, service: object) -> None:
        self.resources[name] = service

    def register_command(self, name: str, handler: object) -> None:
        self.commands[name] = handler

    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **scope: object,
    ) -> None:
        del context, action, resource_ref, scope

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        **scope: object,
    ) -> bool:
        del context, action, resource_ref, scope
        return True


def test_control_plane_registration_resolves_workspace_inventory_server_side() -> None:
    async def scenario() -> None:
        owner = OwnerRef(type="user", id="template-owner")
        workspace_id = "workspace-owned"
        repository = InMemoryTemplateRepository()
        registry = ContextualTemplateHandlerRegistry()
        registry.register(_AgentHandler())
        application = TemplateApplicationService(repository, registry)
        draft = application.templates.create_draft(
            owner_ref=owner,
            content=TemplateContent(
                name="Workspace-bound agent",
                description="Requires one existing canonical workspace",
                template_type=TemplateType.AGENT,
                configuration=TemplateConfiguration(payload={"profile": {"name": "Agent"}}),
                requirements=TemplateRequirements(
                    workspace_prerequisites=(workspace_id,),
                ),
                provenance=TemplateProvenance(
                    author="user:template-owner",
                    source="test",
                    trust=TemplateTrust.LOCAL,
                ),
            ),
        )
        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        control_plane = _ControlPlane(_WorkspaceProvider(workspace_id, owner))
        register_template_control_plane(control_plane, application)  # type: ignore[arg-type]
        preview = control_plane.commands["template.preview"]
        context = RequestContext(
            request_id="template-workspace-preview",
            correlation_id="template-workspace-preview",
            actor=ActorContext(
                principal_ref="user:template-owner",
                owner_type=owner.type,
                owner_id=owner.id,
            ),
        )

        result = await preview(context, published.template_id, {})  # type: ignore[operator]

        assert result["applicable"] is True
        assert result["missing_workspace_prerequisites"] == []

    asyncio.run(scenario())
