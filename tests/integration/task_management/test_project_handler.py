from __future__ import annotations

import asyncio

from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    ProjectTemplateExporter,
    TemplateApplicationService,
    TemplateEnvironment,
    register_project_template_handler,
)


def test_project_template_export_publish_and_apply_rebinds_identity() -> None:
    async def scenario() -> None:
        scopes = ScopeStore()
        source = scopes.create_project(
            key="source-project",
            name="Reusable research project",
            owner_type="user",
            owner_id="source-owner",
        )
        handlers = ContextualTemplateHandlerRegistry()
        register_project_template_handler(handlers, scopes)
        application = TemplateApplicationService(InMemoryTemplateRepository(), handlers)
        exporter = ProjectTemplateExporter(scopes, application.templates)

        draft = exporter.create_from_project(
            source.id,
            owner_ref=OwnerRef(type="user", id="template-owner"),
            author="user:author",
        )
        assert draft.content.configuration.payload == {"name": source.name}
        assert draft.content.provenance.metadata["source_resource_id"] == source.id
        assert source.id not in draft.content.configuration.payload.values()

        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        preview = application.preview(
            draft.template_id,
            applied_by=OwnerRef(type="user", id="destination-owner"),
            environment=TemplateEnvironment(),
        )
        assert preview.applicable is True
        assert len(preview.resource_changes) == 1
        assert preview.resource_changes[0].resource_type == "project"

        instance = await application.apply(
            draft.template_id,
            applied_by=OwnerRef(type="user", id="destination-owner"),
            environment=TemplateEnvironment(),
            revision=published.revision,
        )
        assert len(instance.resource_refs) == 1
        created_ref = instance.resource_refs[0]
        assert created_ref.resource_type == "project"
        assert created_ref.resource_id != source.id

        created = scopes.get_project(created_ref.resource_id)
        assert created.name == source.name
        assert created.owner_ref == OwnerRef(type="user", id="destination-owner")
        assert scopes.get_project(source.id).owner_ref == OwnerRef(
            type="user",
            id="source-owner",
        )

    asyncio.run(scenario())
