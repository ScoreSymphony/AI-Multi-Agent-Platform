from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
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
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateEnvironment, TemplateService


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="issue-78-owner")


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-issue-78-reopened-p0",
        correlation_id="correlation-issue-78-reopened-p0",
        actor=ActorContext(
            principal_ref="user:issue-78-owner",
            owner_type="user",
            owner_id="issue-78-owner",
        ),
        idempotency_key="issue-78-reopened-p0",
    )


def _content(
    name: str,
    template_type: TemplateType,
    *,
    dependencies: tuple[TemplateDependency, ...] = (),
) -> TemplateContent:
    return TemplateContent(
        name=name,
        description=f"{name} template",
        template_type=template_type,
        configuration=TemplateConfiguration(payload={"name": name}),
        dependencies=dependencies,
        provenance=TemplateProvenance(author="test", source="test"),
    )


@dataclass
class _AgentHandler:
    created: list[str]
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
        resource_id = f"agent-{context.instance_id}-{len(self.created) + 1}"
        self.created.append(resource_id)
        return (TemplateResourceRef(resource_type="agent", resource_id=resource_id),)


@dataclass
class _AuthorizationControlPlane:
    deny_refs: set[str] = field(default_factory=set)
    calls: list[dict[str, object]] = field(default_factory=list)

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
        del context
        self.calls.append(
            {
                "action": action,
                "resource_ref": resource_ref,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "project_id": project_id,
                "request_payload_digest": request_payload_digest,
            }
        )
        if resource_ref in self.deny_refs:
            raise ContractError(ErrorCode.FORBIDDEN, f"forbidden resource: {resource_ref}")

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
        del context, action, owner_type, owner_id, project_id, request_payload_digest
        return resource_ref not in self.deny_refs


def _application(
    repository: InMemoryTemplateRepository,
    handler: _AgentHandler,
) -> TemplateApplicationService:
    registry = ContextualTemplateHandlerRegistry()
    registry.register(handler)
    return TemplateApplicationService(repository, registry)


def test_reapply_authorizes_instance_and_exact_source_revision() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        service = TemplateService(repository)
        draft = service.create_draft(
            owner_ref=_owner(),
            content=_content("Agent v1", TemplateType.AGENT),
        )
        first_published = service.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        handler = _AgentHandler(created=[])
        application = _application(repository, handler)
        first = await application.apply(
            first_published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=first_published.revision,
        )

        next_draft = service.revise_draft(
            draft.template_id,
            _content("Agent v2", TemplateType.AGENT),
            expected_revision=first_published.revision,
        )
        latest_published = service.publish(
            draft.template_id,
            expected_revision=next_draft.revision,
        )
        assert latest_published.revision != first.source.revision

        authorization = _AuthorizationControlPlane()
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, authorization)),
        )

        result = await handlers.reapply_template(_context(), first.instance_id, {})
        second_id = result["id"]
        assert isinstance(second_id, str)
        second = repository.get_instantiation(second_id)

        assert second.instance_id != first.instance_id
        assert second.source == first.source
        assert second.resource_refs != first.resource_refs
        assert repository.get_instantiation(first.instance_id) == first
        assert [call["resource_ref"] for call in authorization.calls] == [
            first.instance_id,
            first.source.template_id,
        ]
        assert all(call["action"] == "template.reapply" for call in authorization.calls)

    asyncio.run(scenario())


def test_direct_service_reapply_defaults_to_exact_source_revision() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        service = TemplateService(repository)
        draft = service.create_draft(
            owner_ref=_owner(),
            content=_content("Agent v1", TemplateType.AGENT),
        )
        first_published = service.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        handler = _AgentHandler(created=[])
        application = _application(repository, handler)
        first = await application.apply(
            first_published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=first_published.revision,
        )

        next_draft = service.revise_draft(
            draft.template_id,
            _content("Agent v2", TemplateType.AGENT),
            expected_revision=first_published.revision,
        )
        service.publish(draft.template_id, expected_revision=next_draft.revision)

        second = await application.reapply(
            first.instance_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        assert second.source == first.source
        assert second.instance_id != first.instance_id

    asyncio.run(scenario())


@pytest.mark.parametrize("command", ["preview", "apply"])
def test_unauthorized_dependency_blocks_preview_and_apply_before_resource_creation(
    command: str,
) -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        service = TemplateService(repository)
        dependency_draft = service.create_draft(
            owner_ref=_owner(),
            content=_content("Dependency", TemplateType.AGENT),
        )
        dependency = service.publish(
            dependency_draft.template_id,
            expected_revision=dependency_draft.revision,
        )
        root_draft = service.create_draft(
            owner_ref=_owner(),
            content=_content(
                "Composite root",
                TemplateType.COMPOSITE,
                dependencies=(
                    TemplateDependency(
                        template_id=dependency.template_id,
                        revision=dependency.revision,
                    ),
                ),
            ),
        )
        root = service.publish(
            root_draft.template_id,
            expected_revision=root_draft.revision,
        )

        handler = _AgentHandler(created=[])
        application = _application(repository, handler)
        authorization = _AuthorizationControlPlane(deny_refs={dependency.template_id})
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, authorization)),
        )

        with pytest.raises(ContractError) as exc_info:
            if command == "preview":
                await handlers.preview_template(_context(), root.template_id, {})
            else:
                await handlers.apply_template(_context(), root.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert repository.list_instantiations(root.template_id) == ()
        assert [call["resource_ref"] for call in authorization.calls] == [
            dependency.template_id,
        ]

    asyncio.run(scenario())
