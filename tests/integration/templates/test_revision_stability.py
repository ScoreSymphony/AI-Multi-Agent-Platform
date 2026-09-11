from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateInstantiationContext,
    TemplateInstantiationProvenance,
    TemplateProvenance,
    TemplateResourceChange,
    TemplateResourceRef,
    TemplateRevision,
    TemplateType,
)
from ai_multi_agent_platform.templates.service import TemplateService


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="template-revision-user")


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
        provenance=TemplateProvenance(author="test", source="revision-stability"),
    )


@dataclass
class _RecordingAgentHandler:
    instantiated: list[tuple[int, str]]
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
        self.instantiated.append((revision.revision, revision.content.name))
        return (
            TemplateResourceRef(
                resource_type="agent",
                resource_id=(
                    f"agent-from-revision-{revision.revision}-instance-{context.instance_id}"
                ),
            ),
        )


def test_composite_reapply_keeps_original_revision_until_explicit_upgrade() -> None:
    async def scenario() -> None:
        repository = InMemoryTemplateRepository()
        templates = TemplateService(repository)

        agent_draft_v1 = templates.create_draft(
            owner_ref=_owner(),
            content=_content("Worker v1", TemplateType.AGENT),
        )
        agent_v1 = templates.publish(
            agent_draft_v1.template_id,
            expected_revision=agent_draft_v1.revision,
        )
        composite_draft_v1 = templates.create_draft(
            owner_ref=_owner(),
            content=_content(
                "Composite v1",
                TemplateType.COMPOSITE,
                dependencies=(
                    TemplateDependency(
                        template_id=agent_v1.template_id,
                        revision=agent_v1.revision,
                    ),
                ),
            ),
        )
        composite_v1 = templates.publish(
            composite_draft_v1.template_id,
            expected_revision=composite_draft_v1.revision,
        )

        handler = _RecordingAgentHandler(instantiated=[])
        registry = ContextualTemplateHandlerRegistry()
        registry.register(handler)
        application = TemplateApplicationService(repository, registry)

        old_preview = application.preview(
            composite_v1.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=composite_v1.revision,
        )
        assert old_preview.dependency_order == (agent_v1.ref, composite_v1.ref)

        first = await application.apply(
            composite_v1.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=composite_v1.revision,
        )
        original_first = repository.get_instantiation(first.instance_id)

        agent_draft_v2 = templates.revise_draft(
            agent_v1.template_id,
            _content("Worker v2", TemplateType.AGENT),
            expected_revision=agent_v1.revision,
        )
        agent_v2 = templates.publish(
            agent_v1.template_id,
            expected_revision=agent_draft_v2.revision,
        )
        composite_draft_v2 = templates.revise_draft(
            composite_v1.template_id,
            _content(
                "Composite v2",
                TemplateType.COMPOSITE,
                dependencies=(
                    TemplateDependency(
                        template_id=agent_v2.template_id,
                        revision=agent_v2.revision,
                    ),
                ),
            ),
            expected_revision=composite_v1.revision,
        )
        composite_v2 = templates.publish(
            composite_v1.template_id,
            expected_revision=composite_draft_v2.revision,
        )

        new_preview = application.preview(
            composite_v2.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=composite_v2.revision,
        )
        assert new_preview.dependency_order == (agent_v2.ref, composite_v2.ref)

        reapplied = await application.reapply(
            first.instance_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        upgraded = await application.reapply(
            first.instance_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
            revision=composite_v2.revision,
        )

        assert first.source == composite_v1.ref
        assert reapplied.source == composite_v1.ref
        assert upgraded.source == composite_v2.ref
        assert handler.instantiated == [
            (agent_v1.revision, "Worker v1"),
            (agent_v1.revision, "Worker v1"),
            (agent_v2.revision, "Worker v2"),
        ]
        assert repository.get_instantiation(first.instance_id) == original_first == first
        assert first.resource_refs != reapplied.resource_refs
        assert first.resource_refs != upgraded.resource_refs

    asyncio.run(scenario())
