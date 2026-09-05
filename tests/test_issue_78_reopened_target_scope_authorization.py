from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, Project, new_id
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
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateService


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="issue-78-target-owner")


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-issue-78-target-scope",
        correlation_id="correlation-issue-78-target-scope",
        actor=ActorContext(
            principal_ref="user:issue-78-target-owner",
            owner_type="user",
            owner_id="issue-78-target-owner",
        ),
        idempotency_key="issue-78-target-scope",
    )


@dataclass
class _RecordingHandler:
    template_type: TemplateType
    created: list[str]

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        return (
            TemplateResourceChange(
                resource_type=self.template_type.value,
                action="create",
                description=f"Preview {revision.template_id}",
            ),
        )

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance
        resource_id = f"created-{context.instance_id}"
        self.created.append(resource_id)
        return (
            TemplateResourceRef(
                resource_type=self.template_type.value,
                resource_id=resource_id,
            ),
        )


@dataclass
class _Scopes:
    projects: dict[str, Project]

    def get_project(self, project_id: str) -> Project:
        try:
            return self.projects[project_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"project not found: {project_id}") from exc


@dataclass(frozen=True)
class _Workspace:
    id: str
    owner_ref: OwnerRef
    project_id: str


@dataclass
class _WorkspaceProvider:
    workspaces: dict[str, _Workspace]

    async def get_workspace(self, workspace_id: str) -> _Workspace:
        try:
            return self.workspaces[workspace_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}") from exc


@dataclass
class _ScopedControlPlane:
    scopes: _Scopes
    workspace_provider: _WorkspaceProvider | None = None
    deny_refs: set[str] = field(default_factory=set)
    authorization_calls: list[str] = field(default_factory=list)

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
        del context, action, owner_type, owner_id, project_id, request_payload_digest
        self.authorization_calls.append(resource_ref)
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


def _published_application(
    template_type: TemplateType,
    payload: dict[str, object],
) -> tuple[TemplateApplicationService, TemplateRevision, _RecordingHandler]:
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=_owner(),
        content=TemplateContent(
            name="Target scope regression",
            description="Manually authored scope-bearing Template",
            template_type=template_type,
            configuration=TemplateConfiguration(payload=payload),
            provenance=TemplateProvenance(author="test", source="test"),
        ),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    handler = _RecordingHandler(template_type=template_type, created=[])
    registry = ContextualTemplateHandlerRegistry()
    registry.register(handler)
    return TemplateApplicationService(repository, registry), published, handler


def test_agent_target_project_is_authorized_before_resource_creation() -> None:
    async def scenario() -> None:
        project = Project(name="Protected target", owner_ref=_owner())
        application, published, handler = _published_application(
            TemplateType.AGENT,
            {"project_id": project.id},
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            deny_refs={project.id},
        )
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
        )

        with pytest.raises(ContractError) as exc_info:
            await handlers.apply_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert application.repository.list_instantiations(published.template_id) == ()
        assert control_plane.authorization_calls == [published.template_id, project.id]

    asyncio.run(scenario())


def test_agent_workspace_default_is_authorized_before_resource_creation() -> None:
    async def scenario() -> None:
        project = Project(name="Workspace project", owner_ref=_owner())
        workspace_id = new_id("workspace")
        workspace = _Workspace(
            id=workspace_id,
            owner_ref=_owner(),
            project_id=project.id,
        )
        application, published, handler = _published_application(
            TemplateType.AGENT,
            {
                "profile": {
                    "workspace_defaults": {
                        "project_id": project.id,
                        "workspace_id": workspace_id,
                    }
                }
            },
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            workspace_provider=_WorkspaceProvider({workspace_id: workspace}),
            deny_refs={workspace_id},
        )
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
        )

        with pytest.raises(ContractError) as exc_info:
            await handlers.apply_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert control_plane.authorization_calls == [
            published.template_id,
            project.id,
            workspace_id,
        ]

    asyncio.run(scenario())


def test_automation_task_scope_is_authorized_before_resource_creation() -> None:
    async def scenario() -> None:
        project = Project(name="Automation task project", owner_ref=_owner())
        application, published, handler = _published_application(
            TemplateType.AUTOMATION,
            {
                "task_template": {
                    "project_id": project.id,
                    "workspace_id": None,
                }
            },
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            deny_refs={project.id},
        )
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
        )

        with pytest.raises(ContractError) as exc_info:
            await handlers.preview_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert control_plane.authorization_calls == [published.template_id, project.id]

    asyncio.run(scenario())


def test_workspace_target_fails_closed_without_workspace_provider() -> None:
    async def scenario() -> None:
        workspace_id = new_id("workspace")
        application, published, handler = _published_application(
            TemplateType.AGENT_TEAM,
            {"workspace_id": workspace_id},
        )
        control_plane = _ScopedControlPlane(scopes=_Scopes({}))
        handlers = TemplateCommandHandlers(
            application,
            scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
        )

        with pytest.raises(ContractError) as exc_info:
            await handlers.apply_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        assert handler.created == []
        assert control_plane.authorization_calls == [published.template_id]

    asyncio.run(scenario())
