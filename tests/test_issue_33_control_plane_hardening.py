from __future__ import annotations

import asyncio

from ai_multi_agent_platform.agents import (
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
)
from ai_multi_agent_platform.agents.control_plane import AgentCommandHandlers
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef, new_id


def _context() -> RequestContext:
    return RequestContext(
        request_id="request-issue-33-hardening",
        correlation_id="correlation-issue-33-hardening",
        actor=ActorContext(
            principal_ref="user:issue-33-actor",
            owner_type="user",
            owner_id="issue-33-owner",
        ),
    )


def _profile(name: str) -> dict[str, object]:
    return {
        "name": name,
        "role": "worker",
        "instructions": {"role": {"content": "Do the work."}},
    }


def test_agent_control_plane_mutations_preserve_scopes_and_record_provenance() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)
        handlers = AgentCommandHandlers(service)
        context = _context()
        first_project = new_id("project")
        first_workspace = new_id("workspace")
        second_workspace = new_id("workspace")

        created = await handlers.create_agent(
            context,
            "agents",
            {
                "profile": _profile("Agent v1"),
                "project_id": first_project,
                "workspace_id": first_workspace,
            },
        )
        agent_id = created["id"]
        assert isinstance(agent_id, str)
        first = repository.get_agent_revision(agent_id, 1)
        assert first.project_id == first_project
        assert first.workspace_id == first_workspace
        assert first.provenance is not None
        assert first.provenance.source == "control-plane"
        assert first.provenance.actor_ref == "user:issue-33-actor"
        assert first.provenance.details["operation"] == "agent.create"

        await handlers.update_agent(
            context,
            agent_id,
            {
                "expected_revision": 1,
                "profile": _profile("Agent v2"),
                "project_id": None,
                "workspace_id": second_workspace,
                "owner_ref": {"type": "organization", "id": "issue-33-org"},
            },
        )
        second = repository.get_agent_revision(agent_id, 2)
        assert second.agent_id == first.agent_id
        assert second.project_id is None
        assert second.workspace_id == second_workspace
        assert second.owner_ref == OwnerRef(type="organization", id="issue-33-org")
        assert repository.get_agent_revision(agent_id, 1) == first
        assert second.provenance is not None
        assert second.provenance.actor_ref == "user:issue-33-actor"
        assert second.provenance.details["operation"] == "agent.update"

        cloned = await handlers.clone_agent(
            context,
            agent_id,
            {
                "revision": 2,
                "name": "Agent clone",
                "project_id": first_project,
                "workspace_id": None,
            },
        )
        clone_id = cloned["id"]
        assert isinstance(clone_id, str)
        clone = repository.get_agent_revision(clone_id, 1)
        assert clone.agent_id != agent_id
        assert clone.project_id == first_project
        assert clone.workspace_id is None
        assert clone.provenance is not None
        assert clone.provenance.details["operation"] == "agent.clone"

        await handlers.rollback_agent(
            context,
            agent_id,
            {"target_revision": 1, "expected_revision": 2},
        )
        rollback = repository.get_agent_revision(agent_id, 3)
        assert rollback.project_id == first.project_id
        assert rollback.workspace_id == first.workspace_id
        assert rollback.owner_ref == first.owner_ref
        assert rollback.provenance is not None
        assert rollback.provenance.details["operation"] == "agent.rollback"

    asyncio.run(scenario())


def test_team_control_plane_mutations_preserve_scopes_and_record_provenance() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)
        handlers = AgentCommandHandlers(service)
        context = _context()
        project_id = new_id("project")
        workspace_id = new_id("workspace")

        agent = service.create_agent(
            profile=service.create_agent.__annotations__["profile"](
                name="placeholder",
                role="placeholder",
                instructions=None,
            ),
            owner_ref=OwnerRef(type="user", id="unused"),
        )

    asyncio.run(scenario())
