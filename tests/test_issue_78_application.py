from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates.application import (
    ContextualTemplateHandlerRegistry,
    TemplateApplicationService,
    TemplateInstantiationContext,
)
from ai_multi_agent_platform.templates.models import (
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateRevisionRef,
    TemplateType,
)
from ai_multi_agent_platform.templates.repository import InMemoryTemplateRepository
from ai_multi_agent_platform.templates.service import TemplateEnvironment, TemplateService


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="template-application-user")


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
        assert provenance.source == revision.ref
        resource_id = f"agent-from-{context.instance_id}-{len(self.created) + 1}"
        self.created.append(resource_id)
        return (TemplateResourceRef(resource_type="agent", resource_id=resource_id),)


@dataclass
class _TeamHandler:
    dependency_template_id: str
    resolved_agent_ids: list[str]
    template_type = TemplateType.AGENT_TEAM

    def preview(self, revision: TemplateRevision) -> tuple[TemplateResourceChange, ...]:
        del revision
        return (TemplateResourceChange(resource_type="agent_team", action="create"),)

    async def instantiate(
        self,
        revision: TemplateRevision,
        provenance: TemplateInstantiationProvenance,
        context: TemplateInstantiationContext,
    ) -> tuple[TemplateResourceRef, ...]:
        del revision, provenance
        agent = context.single_resource_for(
            self.dependency_template_id,
            resource_type="agent",
        )
        self.resolved_agent_ids.append(agent.resource_id)
        return (
            TemplateResourceRef(
                resource_type="agent_team",
                resource_id=f"team-using-{agent.resource_id}",
            ),
        )


def test_dependency_created_resource_ids_are_available_to_later_handlers() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        templates = TemplateService(repository)

        agent_draft = templates.create_draft(
            owner_ref=_owner(),
            content=_content("Worker", TemplateType.AGENT),
        )
        agent_published = templates.publish(
            agent_draft.template_id,
            expected_revision=agent_draft.revision,
        )
        team_draft = templates.create_draft(
            owner_ref=_owner(),
            content=_content(
                "Team",
                TemplateType.AGENT_TEAM,
                dependencies=(
                    TemplateDependency(
                        template_id=agent_published.template_id,
                        revision=agent_published.revision,
                    ),
                ),
            ),
        )
        team_published = templates.publish(
            team_draft.template_id,
            expected_revision=team_draft.revision,
        )

        agent_handler = _AgentHandler(created=[])
        team_handler = _TeamHandler(
            dependency_template_id=agent_published.template_id,
            resolved_agent_ids=[],
        )
        registry = ContextualTemplateHandlerRegistry()
        registry.register(agent_handler)
        registry.register(team_handler)
        application = TemplateApplicationService(repository, registry)

        preview = application.preview(
            team_published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        assert preview.applicable is True
        assert [item.resource_type for item in preview.resource_changes] == [
            "agent",
            "agent_team",
        ]

        instance = await application.apply(
            team_published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )

        assert team_handler.resolved_agent_ids == [agent_handler.created[0]]
        assert instance.resource_refs[0].resource_id == agent_handler.created[0]
        assert instance.resource_refs[1].resource_id == f"team-using-{agent_handler.created[0]}"
        assert repository.get_instantiation(instance.instance_id) == instance
        assert repository.list_instantiations(team_published.template_id) == (instance,)

    asyncio.run(scenario())


def test_reapply_creates_new_instance_without_mutating_previous_instance() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        templates = TemplateService(repository)
        draft = templates.create_draft(
            owner_ref=_owner(),
            content=_content("Agent", TemplateType.AGENT),
        )
        published = templates.publish(draft.template_id, expected_revision=draft.revision)

        handler = _AgentHandler(created=[])
        registry = ContextualTemplateHandlerRegistry()
        registry.register(handler)
        application = TemplateApplicationService(repository, registry)

        first = await application.apply(
            published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        second = await application.reapply(
            first.instance_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )

        assert first.instance_id != second.instance_id
        assert first.resource_refs != second.resource_refs
        assert repository.get_instantiation(first.instance_id) == first
        assert repository.get_instantiation(second.instance_id) == second
        assert repository.list_instantiations(published.template_id) == (first, second)

    asyncio.run(scenario())


def test_context_requires_revision_pin_when_same_template_has_multiple_applied_revisions() -> None:
    template_id = new_id("template")
    context = TemplateInstantiationContext(
        instance_id=new_id("template_instance"),
        environment=TemplateEnvironment(),
        created_resources={
            TemplateRevisionRef(template_id, 1): (TemplateResourceRef("agent", "agent-one"),),
            TemplateRevisionRef(template_id, 2): (TemplateResourceRef("agent", "agent-two"),),
        },
    )

    with pytest.raises(ContractError) as exc_info:
        context.resources_for(template_id, resource_type="agent")
    assert exc_info.value.code is ErrorCode.CONFLICT

    pinned = context.single_resource_for(
        template_id,
        revision=2,
        resource_type="agent",
    )
    assert pinned.resource_id == "agent-two"
