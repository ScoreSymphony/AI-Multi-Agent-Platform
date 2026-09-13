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
from ai_multi_agent_platform.templates.environment import PlatformTemplateEnvironmentResolver
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

OWNER = OwnerRef(type="user", id="issue-78-target-owner")


def _context() -> RequestContext:
    return RequestContext(
        request_id="issue-78-integrated-target",
        correlation_id="issue-78-integrated-target",
        actor=ActorContext(
            principal_ref="user:issue-78-target-owner",
            owner_type="user",
            owner_id="issue-78-target-owner",
            actor_type="human",
        ),
        idempotency_key="issue-78-integrated-target",
    )


@dataclass
class _RecordingHandler:
    created: list[str] = field(default_factory=list)
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
        resource_id = f"created-{context.instance_id}"
        self.created.append(resource_id)
        return (TemplateResourceRef(resource_type="agent", resource_id=resource_id),)


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
            raise ContractError(
                ErrorCode.NOT_FOUND, f"workspace not found: {workspace_id}"
            ) from exc


@dataclass
class _ScopedControlPlane:
    scopes: _Scopes
    workspace_provider: _WorkspaceProvider | None = None
    deny_refs: set[str] = field(default_factory=set)
    calls: list[str] = field(default_factory=list)

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
        self.calls.append(resource_ref)
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


def _published_agent(
    configuration: TemplateConfiguration,
    *,
    requirements: TemplateRequirements | None = None,
) -> tuple[TemplateApplicationService, TemplateRevision, _RecordingHandler]:
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OWNER,
        content=TemplateContent(
            name="Target scope integration",
            description="Target scope integration",
            template_type=TemplateType.AGENT,
            configuration=configuration,
            requirements=requirements or TemplateRequirements(),
            provenance=TemplateProvenance(author="test", source="test"),
        ),
    )
    published = service.publish(draft.template_id, expected_revision=draft.revision)
    handler = _RecordingHandler()
    registry = ContextualTemplateHandlerRegistry()
    registry.register(handler)
    return TemplateApplicationService(repository, registry), published, handler


def _handlers(
    application: TemplateApplicationService,
    control_plane: _ScopedControlPlane,
    *,
    environment_resolver: PlatformTemplateEnvironmentResolver | None = None,
) -> TemplateCommandHandlers:
    return TemplateCommandHandlers(
        application,
        environment_resolver=environment_resolver,
        scope_access=TemplateScopeAccess(cast(ControlPlane, control_plane)),
    )


def test_literal_project_target_is_authorized_before_creation() -> None:
    async def scenario() -> None:
        project = Project(name="Literal target", owner_ref=OWNER)
        application, published, handler = _published_agent(
            TemplateConfiguration(payload={"project_id": project.id})
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            deny_refs={project.id},
        )

        with pytest.raises(ContractError) as exc_info:
            await _handlers(application, control_plane).apply_template(
                _context(), published.template_id, {}
            )

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert control_plane.calls == [published.template_id, project.id]

    asyncio.run(scenario())


def test_placeholder_bound_project_target_is_authorized_after_materialization() -> None:
    async def scenario() -> None:
        project = Project(name="Bound target", owner_ref=OWNER)
        application, published, handler = _published_agent(
            TemplateConfiguration(payload={"project_id": "${target_project}"}),
            requirements=TemplateRequirements(placeholders=("target_project",)),
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            deny_refs={project.id},
        )
        resolver = PlatformTemplateEnvironmentResolver(
            placeholder_bindings=lambda _: {"target_project": project.id}
        )

        with pytest.raises(ContractError) as exc_info:
            await _handlers(
                application,
                control_plane,
                environment_resolver=resolver,
            ).preview_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert control_plane.calls == [published.template_id, project.id]

    asyncio.run(scenario())


def test_external_configuration_project_target_is_authorized_after_dereference() -> None:
    async def scenario() -> None:
        project = Project(name="Referenced target", owner_ref=OWNER)
        reference = "config://issue-78/agent-target"
        application, published, handler = _published_agent(
            TemplateConfiguration(reference=reference)
        )
        control_plane = _ScopedControlPlane(
            scopes=_Scopes({project.id: project}),
            deny_refs={project.id},
        )
        resolver = PlatformTemplateEnvironmentResolver(
            configuration_payloads=lambda _: {
                reference: {"project_id": project.id, "name": "Referenced"}
            }
        )

        with pytest.raises(ContractError) as exc_info:
            await _handlers(
                application,
                control_plane,
                environment_resolver=resolver,
            ).apply_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.FORBIDDEN
        assert handler.created == []
        assert control_plane.calls == [published.template_id, project.id]

    asyncio.run(scenario())


def test_materialized_workspace_target_fails_closed_without_workspace_provider() -> None:
    async def scenario() -> None:
        workspace_id = new_id("workspace")
        application, published, handler = _published_agent(
            TemplateConfiguration(payload={"workspace_id": "${target_workspace}"}),
            requirements=TemplateRequirements(placeholders=("target_workspace",)),
        )
        control_plane = _ScopedControlPlane(scopes=_Scopes({}))
        resolver = PlatformTemplateEnvironmentResolver(
            placeholder_bindings=lambda _: {"target_workspace": workspace_id}
        )

        with pytest.raises(ContractError) as exc_info:
            await _handlers(
                application,
                control_plane,
                environment_resolver=resolver,
            ).apply_template(_context(), published.template_id, {})

        assert exc_info.value.code is ErrorCode.UNAVAILABLE
        assert handler.created == []
        assert control_plane.calls == [published.template_id]

    asyncio.run(scenario())


def test_caller_cannot_supply_new_server_owned_environment_fields() -> None:
    async def scenario() -> None:
        application, published, _ = _published_agent(
            TemplateConfiguration(payload={"name": "Agent"})
        )
        control_plane = _ScopedControlPlane(scopes=_Scopes({}))
        handlers = _handlers(application, control_plane)

        for field_name, value in (
            ("placeholder_bindings", {"name": "caller"}),
            ("secret_reference_bindings", {"credential": {"secret": "caller"}}),
            ("configuration_payloads", {"config://caller": {"project_id": "caller"}}),
            ("capability_versions", {"capability": ["1"]}),
            ("contract_versions", {"agent": "1"}),
            ("platform_version", "999"),
        ):
            with pytest.raises(ContractError) as exc_info:
                await handlers.preview_template(
                    _context(),
                    published.template_id,
                    {field_name: value},  # type: ignore[dict-item]
                )
            assert exc_info.value.code is ErrorCode.INVALID_REQUEST
            assert field_name in exc_info.value.details["fields"]

    asyncio.run(scenario())
