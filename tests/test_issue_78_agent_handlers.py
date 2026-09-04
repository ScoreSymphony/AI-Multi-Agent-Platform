from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.templates import (
    AgentTemplateExporter,
    ContextualTemplateHandlerRegistry,
    InMemoryTemplateRepository,
    TemplateApplicationService,
    TemplateConfiguration,
    TemplateContent,
    TemplateDependency,
    TemplateEnvironment,
    TemplateProvenance,
    TemplateType,
    register_agent_template_handlers,
)


def _owner() -> OwnerRef:
    return OwnerRef(type="user", id="issue-78-agent-template-user")


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content=f"Act as {name}.")),
        metadata={"portable": True},
    )


def _application() -> tuple[TemplateApplicationService, AgentService]:
    agents = AgentService(InMemoryAgentRepository())
    registry = ContextualTemplateHandlerRegistry()
    register_agent_template_handlers(registry, agents)
    application = TemplateApplicationService(InMemoryTemplateRepository(), registry)
    return application, agents


def test_existing_agent_export_roundtrips_through_canonical_agent_service() -> None:
    async def scenario() -> None:
        application, agents = _application()
        source = agents.create_agent(_profile("Researcher"), owner_ref=_owner())
        exporter = AgentTemplateExporter(agents, application.templates)

        draft = exporter.create_from_agent(
            source.agent_id,
            owner_ref=_owner(),
            author="issue-78-test",
        )
        published = application.templates.publish(
            draft.template_id,
            expected_revision=draft.revision,
        )
        instance = await application.apply(
            published.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )

        assert len(instance.resource_refs) == 1
        created_id = instance.resource_refs[0].resource_id
        assert created_id != source.agent_id
        created = agents.get_agent_revision(created_id)
        assert created.profile == source.profile
        assert created.owner_ref == _owner()
        assert created.provenance is not None
        assert created.provenance.source == "template"
        assert created.provenance.details["template_id"] == published.template_id
        assert created.provenance.details["template_revision"] == published.revision
        assert created.provenance.details["template_instance_id"] == instance.instance_id

        exported_payload = published.content.configuration.payload
        assert exported_payload is not None
        assert "runtime_state" not in exported_payload
        assert "agent_id" not in exported_payload

    asyncio.run(scenario())


def test_team_template_remaps_agent_template_members_leader_and_delegation() -> None:
    async def scenario() -> None:
        application, agents = _application()
        source_a = agents.create_agent(_profile("Planner"), owner_ref=_owner())
        source_b = agents.create_agent(_profile("Reviewer"), owner_ref=_owner())
        exporter = AgentTemplateExporter(agents, application.templates)

        agent_a_draft = exporter.create_from_agent(
            source_a.agent_id,
            owner_ref=_owner(),
            author="issue-78-test",
        )
        agent_a = application.templates.publish(
            agent_a_draft.template_id,
            expected_revision=agent_a_draft.revision,
        )
        agent_b_draft = exporter.create_from_agent(
            source_b.agent_id,
            owner_ref=_owner(),
            author="issue-78-test",
        )
        agent_b = application.templates.publish(
            agent_b_draft.template_id,
            expected_revision=agent_b_draft.revision,
        )

        team_draft = application.templates.create_draft(
            owner_ref=_owner(),
            content=TemplateContent(
                name="Portable Review Team",
                description="Team with Template-based member bindings",
                template_type=TemplateType.AGENT_TEAM,
                configuration=TemplateConfiguration(
                    payload={
                        "profile": {
                            "name": "Portable Review Team",
                            "description": "Created without source Agent IDs",
                            "members": (
                                {
                                    "agent_template_id": agent_a.template_id,
                                    "agent_template_revision": agent_a.revision,
                                    "role": "planner",
                                    "required": True,
                                    "can_delegate_to_template_ids": (agent_b.template_id,),
                                },
                                {
                                    "agent_template_id": agent_b.template_id,
                                    "agent_template_revision": agent_b.revision,
                                    "role": "reviewer",
                                    "required": True,
                                },
                            ),
                            "leader_agent_template_id": agent_a.template_id,
                            "max_parallel_agents": 2,
                        }
                    }
                ),
                dependencies=(
                    TemplateDependency(agent_a.template_id, agent_a.revision),
                    TemplateDependency(agent_b.template_id, agent_b.revision),
                ),
                provenance=TemplateProvenance(author="issue-78-test", source="test"),
            ),
        )
        team_template = application.templates.publish(
            team_draft.template_id,
            expected_revision=team_draft.revision,
        )

        preview = application.preview(
            team_template.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        assert [item.resource_type for item in preview.resource_changes] == [
            "agent",
            "agent",
            "agent_team",
        ]

        instance = await application.apply(
            team_template.template_id,
            applied_by=_owner(),
            environment=TemplateEnvironment(),
        )
        created_agents = [
            item.resource_id for item in instance.resource_refs if item.resource_type == "agent"
        ]
        team_id = next(
            item.resource_id
            for item in instance.resource_refs
            if item.resource_type == "agent_team"
        )
        team = agents.get_team_revision(team_id)

        assert len(created_agents) == 2
        assert set(created_agents).isdisjoint({source_a.agent_id, source_b.agent_id})
        assert [member.agent.agent_id for member in team.profile.members] == created_agents
        assert team.profile.leader_agent_id == created_agents[0]
        assert team.profile.members[0].can_delegate_to == (created_agents[1],)
        assert team.profile.members[0].agent.revision == 1
        assert team.profile.members[1].agent.revision == 1

    asyncio.run(scenario())
