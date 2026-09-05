from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, Project
from ai_multi_agent_platform.templates.access import TemplateScopeAccess
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.codec import template_content_to_json
from ai_multi_agent_platform.templates.control_plane import (
    TemplateCommandHandlers,
    TemplateEnvironmentResolver,
    TemplateResourceService,
)
from ai_multi_agent_platform.templates.models import (
    CapabilityRequirement,
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
from ai_multi_agent_platform.templates.service import TemplateEnvironment


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-template-control-plane",
        correlation_id="correlation-template-control-plane",
        actor=ActorContext(
            principal_ref="user:template-author",
            owner_type="user",
            owner_id="template-author",
        ),
        idempotency_key="template-command-1",
    )


def _content() -> TemplateContent:
    return TemplateContent(
        name="Portable agent",
        description="Agent template exposed through the Control Plane",
        template_type=TemplateType.AGENT,
        configuration=TemplateConfiguration(payload={"profile": {"name": "Portable agent"}}),
        requirements=TemplateRequirements(
            capabilities=(CapabilityRequirement("tool.files.read"),),
        ),
        provenance=TemplateProvenance(
            author="spoofed-author",
            source="spoofed-source",
            trust=TemplateTrust.UNTRUSTED,
        ),
    )


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


class _Resolver(TemplateEnvironmentResolver):
    async def resolve(self, context: RequestContext) -> TemplateEnvironment:
        del context
        return TemplateEnvironment(capability_ids=frozenset({"tool.files.read"}))


@dataclass
class _ProjectScopes:
    project: Project | None

    def get_project(self, project_id: str) -> Project:
        if self.project is None or self.project.id != project_id:
            raise ContractError(ErrorCode.NOT_FOUND, f"project not found: {project_id}")
        return self.project


@dataclass
class _ScopedControlPlane:
    scopes: _ProjectScopes
    deny: bool = False
    authorization_calls: list[dict[str, object]] = field(default_factory=list)

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
        self.authorization_calls.append(
            {
                "action": action,
                "resource_ref": resource_ref,
                "owner_type": owner_type,
                "owner_id": owner_id,
                "project_id": project_id,
                "request_payload_digest": request_payload_digest,
            }
        )
        if self.deny:
            raise ContractError(ErrorCode.FORBIDDEN, "project scope is forbidden")

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
        del context, action, resource_ref, owner_type, owner_id, project_id, request_payload_digest
        return not self.deny


def _application() -> TemplateApplicationService:
    repository = InMemoryTemplateRepository()
    registry = ContextualTemplateHandlerRegistry()
    registry.register(_AgentHandler())
    return TemplateApplicationService(repository, registry)


def test_create_publish_preview_and_apply_use_server_resolved_environment() -> None:
    async def scenario() -> None:
        application = _application()
        handlers = TemplateCommandHandlers(
            application,
            environment_resolver=_Resolver(),
        )
        context = _context()

        created = await handlers.create_template(
            context,
            "templates",
            {"content": template_content_to_json(_content())},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)

        stored = application.repository.get_revision(template_id, 1)
        assert stored.owner_ref == OwnerRef(type="user", id="template-author")
        assert stored.content.provenance.author == "user:template-author"
        assert stored.content.provenance.source == "control-plane:template.create"
        assert stored.content.provenance.trust is TemplateTrust.LOCAL

        published = await handlers.publish_template(
            context,
            template_id,
            {"expected_revision": 1},
        )
        assert published["latest_published_revision"] == 2

        preview = await handlers.preview_template(context, template_id, {})
        assert preview["applicable"] is True
        assert preview["missing_required_capability_ids"] == []

        applied = await handlers.apply_template(context, template_id, {})
        assert applied["type"] == "template-instance"
        assert application.repository.list_instantiations(template_id)

    asyncio.run(scenario())


def test_project_scoped_create_authorizes_canonical_project_before_storage() -> None:
    async def scenario() -> None:
        project = Project(
            name="Protected project",
            owner_ref=OwnerRef(type="user", id="project-owner"),
        )
        scoped_control_plane = _ScopedControlPlane(_ProjectScopes(project))
        application = _application()
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, scoped_control_plane)),
        )
        payload = {
            "project_id": project.id,
            "content": template_content_to_json(_content()),
        }

        created = await handlers.create_template(_context(), "templates", payload)
        template_id = created["id"]
        assert isinstance(template_id, str)
        definition = application.repository.get_template(template_id)
        assert definition.project_id == project.id
        assert scoped_control_plane.authorization_calls == [
            {
                "action": "template.create",
                "resource_ref": project.id,
                "owner_type": "user",
                "owner_id": "project-owner",
                "project_id": project.id,
                "request_payload_digest": scoped_control_plane.authorization_calls[0][
                    "request_payload_digest"
                ],
            }
        ]
        digest = scoped_control_plane.authorization_calls[0]["request_payload_digest"]
        assert isinstance(digest, str) and len(digest) == 64

    asyncio.run(scenario())


def test_project_scoped_create_rejects_unknown_or_forbidden_project_without_storage() -> None:
    async def scenario() -> None:
        application = _application()
        missing_control_plane = _ScopedControlPlane(_ProjectScopes(None))
        missing_handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, missing_control_plane)),
        )
        with pytest.raises(ContractError) as exc_info:
            await missing_handlers.create_template(
                _context(),
                "templates",
                {
                    "project_id": Project(
                        name="Unknown",
                        owner_ref=OwnerRef(type="user", id="owner"),
                    ).id,
                    "content": template_content_to_json(_content()),
                },
            )
        assert exc_info.value.code is ErrorCode.NOT_FOUND
        assert application.repository.list_templates() == ()

        project = Project(
            name="Forbidden project",
            owner_ref=OwnerRef(type="user", id="different-owner"),
        )
        forbidden_control_plane = _ScopedControlPlane(_ProjectScopes(project), deny=True)
        forbidden_handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, forbidden_control_plane)),
        )
        with pytest.raises(ContractError) as exc_info:
            await forbidden_handlers.create_template(
                _context(),
                "templates",
                {
                    "project_id": project.id,
                    "content": template_content_to_json(_content()),
                },
            )
        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert application.repository.list_templates() == ()

    asyncio.run(scenario())


def test_control_plane_rejects_client_supplied_permission_environment() -> None:
    async def scenario() -> None:
        application = _application()
        handlers = TemplateCommandHandlers(application, environment_resolver=_Resolver())
        context = _context()
        created = await handlers.create_template(
            context,
            "templates",
            {"content": template_content_to_json(_content())},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)

        with pytest.raises(ContractError) as exc_info:
            await handlers.preview_template(
                context,
                template_id,
                {"capability_ids": ["tool.files.read"]},
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST

        with pytest.raises(ContractError) as exc_info:
            await handlers.apply_template(
                context,
                template_id,
                {"grantable_permissions": ["admin:*"], "revision": 1},
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST

    asyncio.run(scenario())


def test_template_resource_service_exposes_full_immutable_revision_history() -> None:
    async def scenario() -> None:
        application = _application()
        handlers = TemplateCommandHandlers(application)
        context = _context()
        created = await handlers.create_template(
            context,
            "templates",
            {"content": template_content_to_json(_content())},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)
        await handlers.publish_template(context, template_id, {"expected_revision": 1})

        service = TemplateResourceService(application.repository)
        listed = await service.list_resources(context, PageQuery())
        fetched = await service.get_resource(context, template_id)

        assert len(listed) == 1
        assert fetched["id"] == template_id
        revisions = fetched["revisions"]
        assert isinstance(revisions, list)
        assert [item["revision"] for item in revisions if isinstance(item, dict)] == [1, 2]

    asyncio.run(scenario())
