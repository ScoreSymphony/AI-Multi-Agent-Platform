from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef

PASSWORD = "correct horse battery staple"


def _profile() -> AgentProfile:
    return AgentProfile(
        name="Single-node template source",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Work through the assigned task."),
        ),
    )


def test_single_node_wires_durable_agent_templates_and_control_plane(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        source = deployment.agents.create_agent(_profile(), owner_ref=owner)
        context = RequestContext(
            request_id="request-template-single-node",
            correlation_id="correlation-template-single-node",
            actor=ActorContext(
                principal_ref=admin.user_id,
                owner_type="user",
                owner_id=admin.user_id,
            ),
            idempotency_key="template-single-node-create",
        )

        assert "templates" in deployment.control_plane.registered_collections
        assert "template-instances" in deployment.control_plane.registered_collections
        assert "template.create-from-agent" in deployment.control_plane.registered_commands
        assert "template.apply" in deployment.control_plane.registered_commands

        created = await deployment.control_plane.execute_command(
            context,
            "template.create-from-agent",
            "templates",
            {"agent_id": source.agent_id},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)

        published = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-template-publish",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="template-single-node-publish",
            ),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )
        assert published["latest_published_revision"] == 2

        applied = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-template-apply",
                correlation_id=context.correlation_id,
                actor=context.actor,
                idempotency_key="template-single-node-apply",
            ),
            "template.apply",
            template_id,
            {},
        )
        instance_id = applied["id"]
        assert isinstance(instance_id, str)
        instance = deployment.templates.repository.get_instantiation(instance_id)
        assert len(instance.resource_refs) == 1
        created_agent_id = instance.resource_refs[0].resource_id
        assert created_agent_id != source.agent_id
        assert deployment.agents.get_agent_revision(created_agent_id).profile == source.profile
        assert (config.database_dir / "templates.json").exists()

        restarted = build_single_node_deployment(config)
        restored = restarted.templates.repository.get_template(template_id)
        assert restored.latest_published_revision == 2
        assert restarted.templates.repository.get_instantiation(instance_id) == instance
        assert restarted.agents.get_agent_revision(created_agent_id).profile == source.profile
        assert "template.create-from-agent" in restarted.control_plane.registered_commands

    asyncio.run(scenario())
