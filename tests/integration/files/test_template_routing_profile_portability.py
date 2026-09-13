from __future__ import annotations

from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import ModelRoutingProfileRef, new_model_routing_profile_id
from ai_multi_agent_platform.portability.dependencies import parse_resource_dependency
from ai_multi_agent_platform.portability.model_routing_profile_codecs import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
)
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.routing_profile_reference_codecs import (
    RoutingProfileAwareTemplatePortableCodec,
)
from ai_multi_agent_platform.portability.template_codecs import (
    TEMPLATE_RESOURCE_TYPE,
    snapshot_template,
)
from ai_multi_agent_platform.templates import (
    InMemoryTemplateRepository,
    TemplateConfiguration,
    TemplateContent,
    TemplateRequirements,
    TemplateService,
    TemplateType,
)

OWNER = OwnerRef(type="user", id="user-template-routing-portability")


def test_template_exact_routing_profile_ref_uses_canonical_dependency_and_remapping() -> None:
    source_profile_id = new_model_routing_profile_id()
    target_profile_id = new_model_routing_profile_id()
    exact_ref = ModelRoutingProfileRef(source_profile_id, 4).canonical_ref
    templates = InMemoryTemplateRepository()
    service = TemplateService(templates)
    draft = service.create_draft(
        owner_ref=OWNER,
        template_id=new_id("template"),
        content=TemplateContent(
            name="Routed template",
            description="Uses an exact durable routing profile.",
            template_type=TemplateType.AGENT,
            configuration=TemplateConfiguration(payload={"instructions": "portable"}),
            requirements=TemplateRequirements(model_policy_refs=(exact_ref,)),
        ),
    )

    registry = ResourceSerializerRegistry()
    registry.register(RoutingProfileAwareTemplatePortableCodec())
    resource = registry.serialize(
        TEMPLATE_RESOURCE_TYPE,
        snapshot_template(templates, draft.template_id),
    )

    resource_dependencies = [
        (parse_resource_dependency(item), item)
        for item in resource.dependencies
        if item.kind.value == "resource"
    ]
    profile_dependencies = [
        (reference, item)
        for reference, item in resource_dependencies
        if reference.resource_type == MODEL_ROUTING_PROFILE_RESOURCE_TYPE
    ]
    assert len(profile_dependencies) == 1
    reference, dependency = profile_dependencies[0]
    assert reference.resource_id == source_profile_id
    assert dependency.version_constraint == "==4"
    assert not any(
        reference.resource_type == "model_routing_policy" and reference.resource_id == exact_ref
        for reference, _ in resource_dependencies
    )

    decoded = registry.deserialize(
        resource,
        ImportContext(
            id_mapping={
                (MODEL_ROUTING_PROFILE_RESOURCE_TYPE, source_profile_id): target_profile_id,
            }
        ),
    )
    latest = decoded.revisions[-1]
    assert latest.content.requirements.model_policy_refs == (
        ModelRoutingProfileRef(target_profile_id, 4).canonical_ref,
    )
