from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.connectors import ReferenceConnectorProvider
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.templates import (
    TemplateConfiguration,
    TemplateContent,
    TemplateProvenance,
    TemplateRequirements,
    TemplateType,
)

PASSWORD = "correct horse battery staple"


def _context(admin_id: str, request: str) -> RequestContext:
    return RequestContext(
        request_id=f"request-{request}",
        correlation_id="correlation-issue-78-final",
        actor=ActorContext(
            principal_ref=admin_id,
            actor_type="human",
            owner_type="user",
            owner_id=admin_id,
        ),
        idempotency_key=f"idempotency-{request}",
    )


def test_public_single_node_template_environment_tracks_live_connector_definitions(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        provider = ReferenceConnectorProvider()
        connector_id = provider.definition.id

        draft = deployment.templates.templates.create_draft(
            owner_ref=owner,
            content=TemplateContent(
                name="Connector-dependent composite",
                description="Requires one exact canonical ConnectorDefinition.",
                template_type=TemplateType.COMPOSITE,
                configuration=TemplateConfiguration(payload={}),
                requirements=TemplateRequirements(connector_ids=(connector_id,)),
                provenance=TemplateProvenance(author="test", source="test"),
            ),
        )
        published = deployment.templates.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )

        before = await deployment.control_plane.execute_command(
            _context(admin.user_id, "connector-preview-before"),
            "template.preview",
            published.template_id,
            {"revision": published.revision},
        )
        assert before["applicable"] is False
        assert before["missing_connector_ids"] == [connector_id]

        await deployment.connectors.register_provider(provider)
        persisted = await deployment.connector_repository.list_definitions()
        assert [item.id for item in persisted] == [connector_id]

        after = await deployment.control_plane.execute_command(
            _context(admin.user_id, "connector-preview-after"),
            "template.preview",
            published.template_id,
            {"revision": published.revision},
        )
        assert after["missing_connector_ids"] == []
        assert after["applicable"] is True

        deployment.connector_registry.unregister(
            provider.definition.connector_type_id,
            provider.definition.version,
        )
        removed = await deployment.control_plane.execute_command(
            _context(admin.user_id, "connector-preview-removed"),
            "template.preview",
            published.template_id,
            {"revision": published.revision},
        )
        assert removed["applicable"] is False
        assert removed["missing_connector_ids"] == [connector_id]

    asyncio.run(scenario())


def test_model_routing_profile_create_from_existing_round_trips_policy(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)

        source = await deployment.control_plane.execute_command(
            _context(admin.user_id, "routing-source-create"),
            "model-routing-profile.create",
            "model-routing-profiles",
            {
                "name": "Research routing",
                "description": "Prefer a large local model.",
                "policy": {
                    "requirements": {
                        "min_context_window": 16384,
                        "tool_calling": True,
                        "structured_output": True,
                        "local_only": True,
                        "modalities": ["text"],
                        "reasoning": ["high"],
                    },
                    "preferred_model_ids": ["model-local-large"],
                    "fallback": "fail",
                },
            },
        )
        profile_id = source["id"]
        assert isinstance(profile_id, str)

        exported = await deployment.control_plane.execute_command(
            _context(admin.user_id, "routing-template-export"),
            "template.create-from-model-routing-profile",
            "templates",
            {"profile_id": profile_id},
        )
        template_id = exported["id"]
        assert isinstance(template_id, str)
        draft = deployment.templates.repository.get_revision(template_id, 1)
        assert draft.content.template_type is TemplateType.MODEL_ROUTING_POLICY
        assert draft.content.name == "Research routing"
        assert draft.content.description == "Prefer a large local model."
        assert draft.content.provenance.source == "canonical-model-routing-profile-export"
        assert draft.content.provenance.metadata["source_resource_id"] == profile_id
        assert draft.content.provenance.metadata["source_resource_revision"] == 1

        published = await deployment.control_plane.execute_command(
            _context(admin.user_id, "routing-template-publish"),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )
        published_revision = published["latest_published_revision"]
        assert published_revision == 2

        applied = await deployment.control_plane.execute_command(
            _context(admin.user_id, "routing-template-apply"),
            "template.apply",
            template_id,
            {"revision": published_revision},
        )
        resource_refs = applied["resource_refs"]
        assert isinstance(resource_refs, list)
        assert len(resource_refs) == 1
        created_profile_id = resource_refs[0]["resource_id"]
        assert isinstance(created_profile_id, str)
        assert created_profile_id != profile_id

        source_revision = deployment.routing_profile_repository.get_revision(
            ModelRoutingProfileRef(profile_id, 1)
        )
        created_revision = deployment.routing_profile_repository.get_revision(
            ModelRoutingProfileRef(created_profile_id, 1)
        )
        assert created_revision.name == source_revision.name
        assert created_revision.description == source_revision.description
        assert created_revision.policy == source_revision.policy
        assert created_revision.provenance is not None
        assert created_revision.provenance.source == f"template:{template_id}@{published_revision}"

    asyncio.run(scenario())
