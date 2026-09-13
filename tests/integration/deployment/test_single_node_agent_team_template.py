from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    InstructionSource,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef

_PASSWORD = "correct horse battery staple"


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content=f"Act as {name}."),
        ),
    )


def test_single_node_agent_team_template_roundtrip_and_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
        deployment = build_single_node_deployment(config)
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        owner = OwnerRef(type="user", id=admin.user_id)
        actor = ActorContext(
            principal_ref=admin.user_id,
            owner_type="user",
            owner_id=admin.user_id,
        )

        planner = deployment.agents.create_agent(_profile("Planner"), owner_ref=owner)
        reviewer = deployment.agents.create_agent(_profile("Reviewer"), owner_ref=owner)
        source_team = deployment.agents.create_team(
            AgentTeamProfile(
                name="Portable Review Team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(planner.agent_id, planner.revision),
                        role="planner",
                        can_delegate_to=(reviewer.agent_id,),
                    ),
                    AgentTeamMember(
                        agent=AgentRevisionRef(reviewer.agent_id, reviewer.revision),
                        role="reviewer",
                    ),
                ),
                leader_agent_id=planner.agent_id,
                max_parallel_agents=2,
            ),
            owner_ref=owner,
        )

        assert "template.create-from-agent-team" in deployment.control_plane.registered_commands
        created = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-agent-team-template-create",
                correlation_id="correlation-agent-team-template",
                actor=actor,
                idempotency_key="agent-team-template-create",
            ),
            "template.create-from-agent-team",
            "templates",
            {"team_id": source_team.team_id},
        )
        template_id = created["id"]
        assert isinstance(template_id, str)

        definition = deployment.templates.repository.get_template(template_id)
        draft = deployment.templates.repository.get_revision(
            template_id,
            definition.current_revision,
        )
        assert len(draft.content.dependencies) == 2
        for dependency in draft.content.dependencies:
            assert dependency.revision is not None
            child = deployment.templates.repository.get_revision(
                dependency.template_id,
                dependency.revision,
            )
            assert child.state.value == "published"

        await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-agent-team-template-publish",
                correlation_id="correlation-agent-team-template",
                actor=actor,
                idempotency_key="agent-team-template-publish",
            ),
            "template.publish",
            template_id,
            {"expected_revision": 1},
        )
        applied = await deployment.control_plane.execute_command(
            RequestContext(
                request_id="request-agent-team-template-apply",
                correlation_id="correlation-agent-team-template",
                actor=actor,
                idempotency_key="agent-team-template-apply",
            ),
            "template.apply",
            template_id,
            {},
        )
        instance_id = applied["id"]
        assert isinstance(instance_id, str)
        instance = deployment.templates.repository.get_instantiation(instance_id)
        assert [ref.resource_type for ref in instance.resource_refs] == [
            "agent",
            "agent",
            "agent_team",
        ]

        generated_agent_ids = [
            ref.resource_id for ref in instance.resource_refs if ref.resource_type == "agent"
        ]
        generated_team_id = next(
            ref.resource_id for ref in instance.resource_refs if ref.resource_type == "agent_team"
        )
        assert set(generated_agent_ids).isdisjoint({planner.agent_id, reviewer.agent_id})
        assert generated_team_id != source_team.team_id

        generated_team = deployment.agents.get_team_revision(generated_team_id)
        assert generated_team.owner_ref == owner
        assert [member.agent.agent_id for member in generated_team.profile.members] == (
            generated_agent_ids
        )
        assert generated_team.profile.leader_agent_id == generated_agent_ids[0]
        assert generated_team.profile.members[0].can_delegate_to == (generated_agent_ids[1],)

        restarted = build_single_node_deployment(config)
        restored_team = restarted.agents.get_team_revision(generated_team_id)
        assert restored_team.profile == generated_team.profile
        assert restarted.templates.repository.get_instantiation(instance_id) == instance
        assert "template.create-from-agent-team" in restarted.control_plane.registered_commands

    asyncio.run(scenario())
