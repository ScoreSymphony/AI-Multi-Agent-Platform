from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentModelPolicy,
    AgentPolicyHooks,
    AgentProfile,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    AgentWorkspaceDefaults,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    JsonAgentRepository,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider

OWNER = OwnerRef(type="user", id="issue-33-owner")


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(role=InstructionSource(content="Do the assigned work.")),
    )


def test_json_agent_repository_survives_restart_with_history_team_and_run(
    tmp_path: Path,
) -> None:
    path = tmp_path / "agents.json"
    repository = JsonAgentRepository(path)
    service = AgentService(repository)
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    knowledge_source_id = new_id("knowledge_source")

    first = service.create_agent(
        AgentProfile(
            name="Persistent v1",
            role="researcher",
            instructions=AgentInstructions(
                role=InstructionSource(content="Research carefully.", version="1"),
                platform_constraint_refs=("policy:baseline",),
                project_instruction_refs=("project:research",),
            ),
            model=AgentModelPolicy(
                requirements=RoutingRequirements(
                    explicit_model_id="model-persisted-preference",
                    min_context_window=8_000,
                    tool_calling=True,
                ),
                routing_profile_ref="routing:research",
                allow_task_override=True,
            ),
            capabilities=AgentCapabilityPolicy(
                allowed=("tool.persisted",),
                constraints=(
                    CapabilityConstraint(
                        capability_id="tool.persisted",
                        required=True,
                        exact_version="1.0",
                        approval_ref="approval:sensitive-tool",
                    ),
                ),
            ),
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.AGENT, MemoryScope.USER),
                memory_config_refs=("memory:research",),
                knowledge_source_ids=(knowledge_source_id,),
                allow_user_memory=True,
            ),
            workspace_defaults=AgentWorkspaceDefaults(
                project_id=project_id,
                workspace_id=workspace_id,
            ),
            policy_hooks=AgentPolicyHooks(
                authorization_profile_ref="authorization:research",
                verification_policy_refs=("verification:review",),
            ),
            resource_hints={"cpu": 2},
            metadata={"purpose": "restart-roundtrip"},
        ),
        owner_ref=OWNER,
        project_id=project_id,
        workspace_id=workspace_id,
        provenance=Provenance(
            source="issue-33-test",
            actor_ref="user:test",
            details={"operation": "create"},
        ),
    )
    second_owner = OwnerRef(type="organization", id="issue-33-org")
    second = service.update_agent(
        first.agent_id,
        _profile("Persistent v2"),
        expected_revision=1,
        owner_ref=second_owner,
        project_id=None,
        workspace_id=None,
        provenance=Provenance(source="issue-33-test", details={"operation": "update"}),
    )

    team = service.create_team(
        AgentTeamProfile(
            name="Persistent Team v1",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(first.agent_id, 1),
                    role="researcher",
                ),
            ),
            leader_agent_id=first.agent_id,
            metadata={"revision_marker": 1},
        ),
        owner_ref=OWNER,
        provenance=Provenance(source="issue-33-test", details={"operation": "team-create"}),
    )
    team_second = service.update_team(
        team.team_id,
        AgentTeamProfile(
            name="Persistent Team v2",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(first.agent_id, 2),
                    role="reviewer",
                ),
            ),
            leader_agent_id=first.agent_id,
            metadata={"revision_marker": 2},
        ),
        expected_revision=1,
    )

    runtime = AgentRuntime(service)
    run = asyncio.run(
        runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=second.agent_id,
            revision=2,
        )
    )

    restored = JsonAgentRepository(path)

    assert restored.get_agent(first.agent_id).current_revision == 2
    assert restored.get_agent(first.agent_id).owner_ref == second_owner
    assert restored.get_agent_revision(first.agent_id, 1) == first
    assert restored.get_agent_revision(first.agent_id, 2) == second
    assert restored.get_agent_revision(first.agent_id, 1).owner_ref == OWNER
    assert restored.get_team(team.team_id).current_revision == 2
    assert restored.get_team_revision(team.team_id, 1) == team
    assert restored.get_team_revision(team.team_id, 2) == team_second
    assert restored.get_team_revision(team.team_id, 1).profile.members[0].agent.revision == 1
    assert restored.get_team_revision(team.team_id, 2).profile.members[0].agent.revision == 2
    assert restored.get_agent_run(run.agent_run_id) == run
    assert restored.get_agent_run(run.agent_run_id).agent == AgentRevisionRef(first.agent_id, 2)


def test_missing_required_capability_fails_before_execution() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    agent = service.create_agent(
        AgentProfile(
            name="Capability constrained",
            role="worker",
            instructions=AgentInstructions(role=InstructionSource(content="Work.")),
            capabilities=AgentCapabilityPolicy(
                allowed=("tool.required",),
                constraints=(CapabilityConstraint(capability_id="tool.required", required=True),),
            ),
        ),
        owner_ref=OWNER,
    )
    runtime = AgentRuntime(service)

    with pytest.raises(ContractError) as exc_info:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )

    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_team_and_ownership_updates_preserve_canonical_identity_and_history() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    agent = service.create_agent(_profile("Owned v1"), owner_ref=OWNER)
    new_owner = OwnerRef(type="organization", id="new-owner")
    updated_agent = service.update_agent(
        agent.agent_id,
        _profile("Owned v2"),
        expected_revision=1,
        owner_ref=new_owner,
    )
    team = service.create_team(
        AgentTeamProfile(
            name="Team v1",
            members=(AgentTeamMember(AgentRevisionRef(agent.agent_id, 1), role="worker"),),
        ),
        owner_ref=OWNER,
    )
    updated_team = service.update_team(
        team.team_id,
        AgentTeamProfile(
            name="Team v2",
            members=(AgentTeamMember(AgentRevisionRef(agent.agent_id, 2), role="reviewer"),),
        ),
        expected_revision=1,
        owner_ref=new_owner,
    )

    assert updated_agent.agent_id == agent.agent_id
    assert repository.get_agent_revision(agent.agent_id, 1).owner_ref == OWNER
    assert repository.get_agent_revision(agent.agent_id, 2).owner_ref == new_owner
    assert updated_team.team_id == team.team_id
    assert repository.get_team_revision(team.team_id, 1).profile.name == "Team v1"
    assert repository.get_team_revision(team.team_id, 2).profile.name == "Team v2"
    assert repository.get_team_revision(team.team_id, 1).owner_ref == OWNER
    assert repository.get_team_revision(team.team_id, 2).owner_ref == new_owner


def test_explicit_model_assignment_is_recorded_on_agent_run() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    registry = ModelRegistry()
    provider = FakeModelProvider()
    registry.register_provider(provider)
    registry.register_model(
        ModelConfiguration(
            config_id="model-explicit-agent",
            display_name="Explicit Agent Model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=16_000),
            health=HealthStatus.HEALTHY,
        )
    )
    agent = service.create_agent(
        AgentProfile(
            name="Explicit model agent",
            role="worker",
            instructions=AgentInstructions(role=InstructionSource(content="Work.")),
            model=AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id="model-explicit-agent")
            ),
        ),
        owner_ref=OWNER,
    )

    run = asyncio.run(
        AgentRuntime(service, model_registry=registry).start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    )

    assert run.selected_model_config_id == "model-explicit-agent"
    assert run.selected_provider_id == provider.descriptor.provider_id
