from __future__ import annotations

import asyncio
from collections.abc import Mapping

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.templates import (
    AgentTeamTemplateExporter,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateEnvironment,
    TemplateRevisionState,
    register_agent_template_handlers,
)


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content=f"Act as {name}.")),
    )


def test_agent_team_export_builds_portable_published_agent_dependencies() -> None:
    async def scenario() -> None:
        source_owner = OwnerRef(type="user", id="source-owner")
        destination_owner = OwnerRef(type="user", id="destination-owner")
        source_project_id = new_id("project")
        agents = AgentService(InMemoryAgentRepository())
        first = agents.create_agent(
            _profile("Planner"),
            owner_ref=source_owner,
            project_id=source_project_id,
        )
        second = agents.create_agent(
            _profile("Reviewer"),
            owner_ref=source_owner,
            project_id=source_project_id,
        )
        team = agents.create_team(
            AgentTeamProfile(
                name="Review Team",
                description="Portable team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(first.agent_id, first.revision),
                        role="planner",
                        can_delegate_to=(second.agent_id,),
                    ),
                    AgentTeamMember(
                        agent=AgentRevisionRef(second.agent_id, second.revision),
                        role="reviewer",
                    ),
                ),
                leader_agent_id=first.agent_id,
                max_parallel_agents=2,
            ),
            owner_ref=source_owner,
            project_id=source_project_id,
        )

        registry = ContextualTemplateHandlerRegistry()
        register_agent_template_handlers(registry, agents)
        application = TemplateApplicationService(InMemoryTemplateRepository(), registry)
        exporter = AgentTeamTemplateExporter(agents, application.templates)

        draft = exporter.create_from_team(
            team.team_id,
            owner_ref=destination_owner,
            author="user:exporter",
        )
        assert draft.state is TemplateRevisionState.DRAFT
        assert len(draft.content.dependencies) == 2
        for dependency in draft.content.dependencies:
            assert dependency.revision is not None
            dependency_revision = application.templates.repository.get_revision(
                dependency.template_id,
                dependency.revision,
            )
            assert dependency_revision.state is TemplateRevisionState.PUBLISHED

        payload = draft.content.configuration.payload
        assert payload is not None
        assert first.agent_id not in repr(payload)
        assert second.agent_id not in repr(payload)
        assert source_project_id not in repr(payload)
        profile = payload["profile"]
        assert isinstance(profile, Mapping)
        members = profile["members"]
        assert isinstance(members, tuple)
        first_member = members[0]
        assert isinstance(first_member, Mapping)
        second_member = members[1]
        assert isinstance(second_member, Mapping)
        assert first_member["agent_template_id"] != second_member["agent_template_id"]
        assert first_member["can_delegate_to_template_ids"] == (second_member["agent_template_id"],)
        assert profile["leader_agent_template_id"] == first_member["agent_template_id"]

        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        preview = application.preview(
            published.template_id,
            applied_by=destination_owner,
            environment=TemplateEnvironment(),
        )
        assert [change.resource_type for change in preview.resource_changes] == [
            "agent",
            "agent",
            "agent_team",
        ]

        instance = await application.apply(
            published.template_id,
            applied_by=destination_owner,
            environment=TemplateEnvironment(),
        )
        created_agent_ids = [
            ref.resource_id for ref in instance.resource_refs if ref.resource_type == "agent"
        ]
        created_team_id = next(
            ref.resource_id for ref in instance.resource_refs if ref.resource_type == "agent_team"
        )
        assert len(created_agent_ids) == 2
        assert set(created_agent_ids).isdisjoint({first.agent_id, second.agent_id})
        created_team = agents.get_team_revision(created_team_id)
        assert created_team.owner_ref == destination_owner
        assert created_team.project_id is None
        assert [
            member.agent.agent_id for member in created_team.profile.members
        ] == created_agent_ids
        assert created_team.profile.leader_agent_id == created_agent_ids[0]
        assert created_team.profile.members[0].can_delegate_to == (created_agent_ids[1],)
        for created_agent_id in created_agent_ids:
            created_agent = agents.get_agent_revision(created_agent_id)
            assert created_agent.owner_ref == destination_owner
            assert created_agent.project_id is None
            assert created_agent.workspace_id is None

    asyncio.run(scenario())
