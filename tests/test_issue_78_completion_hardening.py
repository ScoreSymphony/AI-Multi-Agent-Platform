from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentProfile,
    CapabilityConstraint,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import ECHO_CAPABILITY_ID, NativeEchoProvider
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef

PASSWORD = "correct horse battery staple"


def _capability_profile() -> AgentProfile:
    return AgentProfile(
        name="Capability-aware template source",
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Use the required canonical capability."),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(ECHO_CAPABILITY_ID,),
            constraints=(CapabilityConstraint(ECHO_CAPABILITY_ID),),
        ),
    )


def test_single_node_template_preview_uses_live_canonical_capability_inventory(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        )
        admin = deployment.bootstrap_admin("admin", PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        source = deployment.agents.create_agent(_capability_profile(), owner_ref=owner)
        assert deployment.agent_runtime.capability_registry is deployment.capabilities

        actor = ActorContext(
            principal_ref=admin.user_id,
            owner_type="user",
            owner_id=admin.user_id,
        )
        created = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-create",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-create",
            ),
            "template.create-from-agent",
            "templates",
            {"agent_id": source.agent_id},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)
        await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-publish",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-publish",
            ),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )

        before = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-preview-before",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-preview-before",
            ),
            "template.preview",
            template_id,
            {},
        )
        assert before["applicable"] is False
        assert before["missing_required_capability_ids"] == [ECHO_CAPABILITY_ID]

        await deployment.capabilities.register_provider(NativeEchoProvider())

        after = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="template-capability-preview-after",
                correlation_id="template-capability",
                actor=actor,
                idempotency_key="template-capability-preview-after",
            ),
            "template.preview",
            template_id,
            {},
        )
        assert after["applicable"] is True
        assert after["missing_required_capability_ids"] == []

    asyncio.run(scenario())
