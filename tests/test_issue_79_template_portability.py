from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability import (
    IdPolicy,
    ImportContext,
    ResourceSerializerRegistry,
)
from ai_multi_agent_platform.portability.template_codecs import (
    TEMPLATE_RESOURCE_TYPE,
    TemplatePortableCodec,
    register_template_portability_codec,
    snapshot_template,
)
from ai_multi_agent_platform.portability.template_import import TemplateImportMutationHandler
from ai_multi_agent_platform.templates import (
    CapabilityRequirement,
    InMemoryTemplateRepository,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateProvenance,
    TemplateRequirements,
    TemplateService,
    TemplateTrust,
    TemplateType,
)


def _content(
    name: str,
    *,
    dependency_id: str | None = None,
    source_template_id: str | None = None,
) -> TemplateContent:
    dependencies = ()
    if dependency_id is not None:
        dependencies = (TemplateDependency(template_id=dependency_id, revision=2),)
    source_template = None
    if source_template_id is not None:
        from ai_multi_agent_platform.templates import TemplateRevisionRef

        source_template = TemplateRevisionRef(template_id=source_template_id, revision=2)
    return TemplateContent(
        name=name,
        description="portable Template",
        template_type=TemplateType.AGENT,
        configuration=TemplateConfiguration(payload={"instructions": "portable"}),
        dependencies=dependencies,
        requirements=TemplateRequirements(
            capabilities=(
                CapabilityRequirement(
                    capability_id="capability.search",
                    optional=True,
                    version_constraint=">=1",
                ),
            ),
            plugin_ids=("plugin.example",),
            connector_ids=("connector.example",),
            model_policy_refs=("routing-profile-default",),
            secret_reference_placeholders=("MODEL_API_KEY",),
        ),
        provenance=TemplateProvenance(
            author="portable-test",
            source="local",
            trust=TemplateTrust.LOCAL,
            source_template=source_template,
        ),
        tags=("portable",),
    )


def _published_template(
    repository: InMemoryTemplateRepository,
    content: TemplateContent,
    *,
    template_id: str,
    project_id: str,
) -> None:
    service = TemplateService(repository)
    draft = service.create_draft(
        owner_ref=OwnerRef(type="user", id="user-portable"),
        content=content,
        project_id=project_id,
        template_id=template_id,
    )
    service.publish(template_id, expected_revision=draft.revision)


def test_template_codec_preserves_history_dependencies_and_remaps_canonical_refs() -> None:
    source = InMemoryTemplateRepository()
    project_id = new_id("project")
    dependency_id = new_id("template")
    template_id = new_id("template")
    _published_template(
        source,
        _content("dependency"),
        template_id=dependency_id,
        project_id=project_id,
    )
    _published_template(
        source,
        _content(
            "root",
            dependency_id=dependency_id,
            source_template_id=dependency_id,
        ),
        template_id=template_id,
        project_id=project_id,
    )

    registry = ResourceSerializerRegistry()
    register_template_portability_codec(registry, id_policy=IdPolicy.REGENERATE)
    resource = registry.serialize(TEMPLATE_RESOURCE_TYPE, snapshot_template(source, template_id))

    assert resource.resource_version == "2"
    assert resource.id_policy is IdPolicy.REGENERATE
    assert any(
        item.identifier == f"template:{dependency_id}" and item.version_constraint == "==2"
        for item in resource.dependencies
    )
    assert any(item.identifier == "plugin.example" for item in resource.dependencies)
    assert any(item.identifier == "connector.example" for item in resource.dependencies)
    assert any(item.identifier == "capability.search" for item in resource.dependencies)
    assert not any(item.identifier == "MODEL_API_KEY" for item in resource.dependencies)

    target_template_id = new_id("template")
    target_dependency_id = new_id("template")
    target_project_id = new_id("project")
    decoded = registry.deserialize(
        resource,
        ImportContext(
            id_mapping={
                ("template", template_id): target_template_id,
                ("template", dependency_id): target_dependency_id,
                ("project", project_id): target_project_id,
                ("model_routing_policy", "routing-profile-default"): "routing-profile-target",
            }
        ),
    )
    assert decoded.definition.template_id == target_template_id  # type: ignore[attr-defined]
    assert decoded.definition.project_id == target_project_id  # type: ignore[attr-defined]
    latest = decoded.revisions[-1]  # type: ignore[attr-defined]
    assert latest.content.dependencies[0].template_id == target_dependency_id
    assert latest.content.provenance.source_template is not None
    assert latest.content.provenance.source_template.template_id == target_dependency_id
    assert latest.content.requirements.model_policy_refs == ("routing-profile-target",)


def test_template_import_restores_history_as_untrusted_and_guarded_rollback() -> None:
    async def scenario() -> None:
        source = InMemoryTemplateRepository()
        project_id = new_id("project")
        template_id = new_id("template")
        _published_template(
            source,
            _content("root"),
            template_id=template_id,
            project_id=project_id,
        )
        snapshot = snapshot_template(source, template_id)

        registry = ResourceSerializerRegistry()
        registry.register(TemplatePortableCodec())
        resource = registry.serialize(TEMPLATE_RESOURCE_TYPE, snapshot)
        decoded = registry.deserialize(resource)

        target = InMemoryTemplateRepository()
        handler = TemplateImportMutationHandler(target)
        context = ImportContext()
        await handler.preflight(resource, decoded, context)
        token = await handler.apply(resource, decoded, context)

        assert target.get_template(template_id) == snapshot.definition
        imported_revisions = target.list_revisions(template_id)
        assert len(imported_revisions) == len(snapshot.revisions)
        for imported_revision, source_revision in zip(
            imported_revisions,
            snapshot.revisions,
            strict=True,
        ):
            assert imported_revision.content.provenance.trust is TemplateTrust.UNTRUSTED
            assert imported_revision.content.provenance.metadata["imported_source_trust"] == "local"
            assert replace(
                imported_revision,
                content=replace(
                    imported_revision.content,
                    provenance=source_revision.content.provenance,
                ),
            ) == source_revision

        await handler.rollback(resource, decoded, token, context)
        with pytest.raises(ContractError) as exc_info:
            target.get_template(template_id)
        assert exc_info.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_template_portability_rejects_plaintext_secret_payload() -> None:
    repository = InMemoryTemplateRepository()
    service = TemplateService(repository)
    with pytest.raises(ContractError) as exc_info:
        service.create_draft(
            owner_ref=OwnerRef(type="user", id="user-portable"),
            content=TemplateContent(
                name="unsafe",
                description="unsafe",
                template_type=TemplateType.AGENT,
                configuration=TemplateConfiguration(payload={"api_key": "plaintext"}),
            ),
        )
    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
