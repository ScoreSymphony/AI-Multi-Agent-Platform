from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents import (
    AGENT_REPOSITORY_SCHEMA_VERSION,
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    JsonAgentRepository,
)
from ai_multi_agent_platform.capabilities import (
    ECHO_CAPABILITY_ID,
    CapabilityRegistration,
    CapabilityRegistry,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, new_id

OWNER = OwnerRef(type="user", id="issue-33-hardening")
APPROVAL_REF = "approval:sensitive-tool"
SHARED_RESOURCE_REF = "workspace-resource:shared-cache"


class VersionedApprovalEchoProvider(NativeEchoProvider):
    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        registrations = await super().capability_registrations()
        base = registrations[0]
        protected_v1 = replace(
            base,
            capability=replace(
                base.capability,
                version="1.0",
                required_approvals=(APPROVAL_REF,),
            ),
        )
        unprotected_v2 = replace(
            base,
            capability=replace(
                base.capability,
                version="2.0",
                required_approvals=(),
            ),
        )
        return (protected_v1, unprotected_v2)


def _approval_agent(service: AgentService) -> str:
    revision = service.create_agent(
        AgentProfile(
            name="Approval constrained agent",
            role="worker",
            instructions=AgentInstructions(role=InstructionSource(content="Work safely.")),
            capabilities=AgentCapabilityPolicy(
                allowed=(ECHO_CAPABILITY_ID,),
                constraints=(
                    CapabilityConstraint(
                        capability_id=ECHO_CAPABILITY_ID,
                        required=True,
                        exact_version="1.0",
                        approval_ref=APPROVAL_REF,
                    ),
                ),
            ),
        ),
        owner_ref=OWNER,
    )
    return revision.agent_id


def test_agent_approval_requirement_needs_canonical_capability_registry() -> None:
    service = AgentService(InMemoryAgentRepository())
    agent_id = _approval_agent(service)
    runtime = AgentRuntime(service)

    with pytest.raises(ContractError) as exc_info:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent_id,
            available_capability_ids=frozenset({ECHO_CAPABILITY_ID}),
        )

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_agent_approval_requirement_must_match_resolved_capability_policy() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent_id = _approval_agent(service)
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        runtime = AgentRuntime(service, capability_registry=registry)

        with pytest.raises(ContractError) as exc_info:
            runtime.prepare_agent(
                task_id=new_id("task"),
                run_id=new_id("run"),
                agent_id=agent_id,
            )

        assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION
        assert exc_info.value.details["approval_ref"] == APPROVAL_REF

    asyncio.run(scenario())


def test_approval_checked_capability_version_is_pinned_through_mapping_and_run() -> None:
    async def scenario() -> None:
        service = AgentService(InMemoryAgentRepository())
        agent_id = _approval_agent(service)
        registry = CapabilityRegistry()
        await registry.register_provider(VersionedApprovalEchoProvider())
        runtime = AgentRuntime(service, capability_registry=registry)

        record = await runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent_id,
        )

        assert record.capability_ids == (ECHO_CAPABILITY_ID,)
        assert dict(record.capability_versions) == {ECHO_CAPABILITY_ID: "1.0"}
        mapping = record.telemetry["orchestrator_mapping"]
        assert isinstance(mapping, dict)
        assert mapping["capability_versions"] == {ECHO_CAPABILITY_ID: "1.0"}

    asyncio.run(scenario())


def test_memory_and_knowledge_scope_enforcement_rejects_unassigned_access() -> None:
    service = AgentService(InMemoryAgentRepository())
    allowed_source = new_id("knowledge_source")
    denied_source = new_id("knowledge_source")
    revision = service.create_agent(
        AgentProfile(
            name="Scoped agent",
            role="researcher",
            instructions=AgentInstructions(role=InstructionSource(content="Research.")),
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.AGENT,),
                knowledge_source_ids=(allowed_source,),
            ),
        ),
        owner_ref=OWNER,
    )

    service.ensure_memory_scope(revision.agent_id, revision.revision, MemoryScope.AGENT)
    service.ensure_knowledge_source(revision.agent_id, revision.revision, allowed_source)

    with pytest.raises(ContractError) as memory_error:
        service.ensure_memory_scope(revision.agent_id, revision.revision, MemoryScope.USER)
    assert memory_error.value.code is ErrorCode.FORBIDDEN

    with pytest.raises(ContractError) as knowledge_error:
        service.ensure_knowledge_source(revision.agent_id, revision.revision, denied_source)
    assert knowledge_error.value.code is ErrorCode.FORBIDDEN


def test_shared_team_resource_refs_survive_json_repository_restart(tmp_path: Path) -> None:
    path = tmp_path / "agents.json"
    repository = JsonAgentRepository(path)
    service = AgentService(repository)
    agent = service.create_agent(
        AgentProfile(
            name="Persistent team member",
            role="worker",
            instructions=AgentInstructions(role=InstructionSource(content="Work.")),
        ),
        owner_ref=OWNER,
    )
    team = service.create_team(
        AgentTeamProfile(
            name="Persistent shared-resource team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(agent.agent_id, agent.revision),
                    role="worker",
                ),
            ),
            shared_resource_refs=(SHARED_RESOURCE_REF,),
        ),
        owner_ref=OWNER,
    )

    restored = JsonAgentRepository(path)

    assert restored.get_team_revision(team.team_id, 1).profile.shared_resource_refs == (
        SHARED_RESOURCE_REF,
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == AGENT_REPOSITORY_SCHEMA_VERSION == "2"


def test_v1_agent_repository_snapshot_migrates_explicitly_to_v2(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "legacy-agents.json"
        repository = JsonAgentRepository(path)
        service = AgentService(repository)
        agent = service.create_agent(
            AgentProfile(
                name="Legacy agent",
                role="worker",
                instructions=AgentInstructions(role=InstructionSource(content="Work.")),
            ),
            owner_ref=OWNER,
        )
        team = service.create_team(
            AgentTeamProfile(
                name="Legacy team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(agent.agent_id, agent.revision),
                        role="worker",
                    ),
                ),
            ),
            owner_ref=OWNER,
        )
        run = await AgentRuntime(service).start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )

        legacy = json.loads(path.read_text(encoding="utf-8"))
        legacy["schema_version"] = "1"
        for revision in legacy["team_revisions"]:
            revision["profile"].pop("shared_resource_refs", None)
        for record in legacy["agent_runs"]:
            record.pop("capability_versions", None)
        path.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        restored = JsonAgentRepository(path)
        assert restored.get_team_revision(team.team_id, 1).profile.shared_resource_refs == ()
        assert dict(restored.get_agent_run(run.agent_run_id).capability_versions) == {}

        restored_service = AgentService(restored)
        restored_service.update_agent(
            agent.agent_id,
            restored.get_agent_revision(agent.agent_id, 1).profile,
            expected_revision=1,
        )
        rewritten = json.loads(path.read_text(encoding="utf-8"))
        assert rewritten["schema_version"] == "2"

    asyncio.run(scenario())
