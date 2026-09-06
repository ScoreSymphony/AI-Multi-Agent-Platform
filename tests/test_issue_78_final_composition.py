from __future__ import annotations

import asyncio

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileRef,
    ModelRoutingProfileService,
    RoutingProfileFallbackPolicy,
)
from ai_multi_agent_platform.templates import (
    ModelRoutingPolicyTemplateHandler,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateEnvironment,
    TemplateInstantiationContext,
    TemplateInstantiationProvenance,
    TemplateRevision,
    TemplateRevisionState,
    TemplateType,
)


def test_model_routing_template_handler_creates_and_compensates_exact_profile(tmp_path) -> None:
    repository = JsonModelRoutingProfileRepository(tmp_path / "routing-profiles.json")
    handler = ModelRoutingPolicyTemplateHandler(ModelRoutingProfileService(repository))
    owner = OwnerRef(type="user", id="alice")
    revision = TemplateRevision(
        template_id=new_id("template"),
        revision=1,
        state=TemplateRevisionState.PUBLISHED,
        owner_ref=owner,
        content=TemplateContent(
            name="Research routing",
            description="Prefer one canonical model configuration.",
            template_type=TemplateType.MODEL_ROUTING_POLICY,
            configuration=TemplateConfiguration(
                payload={
                    "policy": {
                        "requirements": {
                            "min_context_window": 8192,
                            "tool_calling": True,
                            "local_only": True,
                        },
                        "preferred_model_ids": ["model-local-large"],
                        "fallback": "fail",
                    }
                }
            ),
        ),
    )
    context = TemplateInstantiationContext(
        instance_id=new_id("template_instance"),
        environment=TemplateEnvironment(),
        created_resources={},
    )
    provenance = TemplateInstantiationProvenance(source=revision.ref, applied_by=owner)

    resources = asyncio.run(handler.instantiate(revision, provenance, context))

    assert len(resources) == 1
    resource = resources[0]
    assert resource.resource_type == "model_routing_profile"
    created = repository.get_revision(ModelRoutingProfileRef(resource.resource_id, 1))
    assert created.policy.requirements.min_context_window == 8192
    assert created.policy.requirements.tool_calling is True
    assert created.policy.requirements.local_only is True
    assert created.policy.preferred_model_ids == ("model-local-large",)
    assert created.policy.fallback is RoutingProfileFallbackPolicy.FAIL
    assert created.provenance is not None
    assert created.provenance.source == f"template:{revision.template_id}@1"
    assert created.provenance.details["template_instance_id"] == context.instance_id

    asyncio.run(handler.compensate(resources, provenance, context))

    assert repository.list_definitions() == ()


def test_standard_single_node_exposes_final_template_integrations(tmp_path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )

    collections = set(deployment.control_plane.registered_collections)
    assert {
        "workflows",
        "capability-assignments",
        "model-routing-profiles",
    }.issubset(collections)

    commands = set(deployment.control_plane.registered_commands)
    assert "template.create-from-capability-assignment" in commands
    assert "template.create-from-workflow" in commands

    routing_handler = deployment.templates.handlers.get(TemplateType.MODEL_ROUTING_POLICY)
    assert isinstance(routing_handler, ModelRoutingPolicyTemplateHandler)
    assert routing_handler.service.repository is deployment.routing_profile_repository

    assert isinstance(deployment.templates, TemplateApplicationService)
