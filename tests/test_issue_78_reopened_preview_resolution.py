from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates.access import TemplateScopeAccess
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.control_plane import TemplateCommandHandlers
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateRequirements,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateService

OWNER = OwnerRef(type="user", id="issue-78-preview-owner")


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
        del revision, provenance, context
        return ()


@dataclass
class _ControlPlane:
    async def _authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> None:
        del (
            context,
            action,
            resource_ref,
            owner_type,
            owner_id,
            project_id,
            request_payload_digest,
        )

    async def _allowed(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        owner_type: str | None = None,
        owner_id: str | None = None,
        project_id: str | None = None,
        request_payload_digest: str | None = None,
    ) -> bool:
        del (
            context,
            action,
            resource_ref,
            owner_type,
            owner_id,
            project_id,
            request_payload_digest,
        )
        return True


def test_preview_reports_unresolved_placeholder_instead_of_forcing_materialization() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        service = TemplateService(repository)
        draft = service.create_draft(
            owner_ref=OWNER,
            content=TemplateContent(
                name="Unresolved target",
                description="Preview must report the unresolved binding",
                template_type=TemplateType.AGENT,
                configuration=TemplateConfiguration(payload={"project_id": "${target_project}"}),
                requirements=TemplateRequirements(placeholders=("target_project",)),
                provenance=TemplateProvenance(author="test", source="test"),
            ),
        )
        published = service.publish(draft.template_id, expected_revision=draft.revision)
        registry = ContextualTemplateHandlerRegistry()
        registry.register(_AgentHandler())
        application = TemplateApplicationService(repository, registry)
        control_plane = _ControlPlane()
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
        )
        context = RequestContext(
            request_id="issue-78-unresolved-preview",
            correlation_id="issue-78-unresolved-preview",
            actor=ActorContext(
                principal_ref="user:issue-78-preview-owner",
                owner_type="user",
                owner_id="issue-78-preview-owner",
                actor_type="human",
            ),
            idempotency_key="issue-78-unresolved-preview",
        )

        preview = await handlers.preview_template(context, published.template_id, {})

        assert preview["applicable"] is False
        assert preview["unresolved_placeholders"] == ["target_project"]

    asyncio.run(scenario())
