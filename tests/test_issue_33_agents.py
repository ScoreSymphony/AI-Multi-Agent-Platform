from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentDataAccess,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRevisionRef,
    AgentRunStatus,
    AgentRuntime,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InMemoryAgentRepository,
    InstructionSource,
    ModelFallbackPolicy,
    OrchestratorMapping,
    UnavailableMemberPolicy,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistry,
    ECHO_CAPABILITY_ID,
    NativeEchoProvider,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, HealthStatus
from ai_multi_agent_platform.data import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
    RoutingRequirements,
)
from ai_multi_agent_platform.testing import FakeModelProvider

OWNER = OwnerRef(type="user", id="user-issue-33")


def profile(
    name: str = "Coder",
    *,
    role: str = "coder",
    enabled: bool = True,
    model: AgentModelPolicy | None = None,
    capabilities: AgentCapabilityPolicy | None = None,
    data_access: AgentDataAccess | None = None,
) -> AgentProfile:
    return AgentProfile(
        name=name,
        role=role,
        instructions=AgentInstructions(
            role=InstructionSource(content=f"Act as {role}.", version="1"),
            platform_constraint_refs=("policy:baseline",),
        ),
        model=model or AgentModelPolicy(),
        capabilities=capabilities or AgentCapabilityPolicy(),
        data_access=data_access or AgentDataAccess(),
        enabled=enabled,
    )


def stack() -> tuple[InMemoryAgentRepository, AgentService, AgentRuntime]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    return repository, service, AgentRuntime(service)


def test_agent_updates_create_immutable_revisions_and_rollback_is_a_new_revision() -> None:
    repository, service, _ = stack()
    first = service.create_agent(profile("First"), owner_ref=OWNER)
    second = service.update_agent(
        first.agent_id,
        profile("Second"),
        expected_revision=1,
        project_id=new_id("project"),
    )
    rolled_back = service.rollback_agent(first.agent_id, 1, expected_revision=2)

    assert first.revision == 1
    assert second.revision == 2
    assert rolled_back.revision == 3
    assert rolled_back.profile.name == "First"
    assert rolled_back.project_id is None
    assert repository.get_agent_revision(first.agent_id, 2).profile.name == "Second"
    assert repository.get_agent_revision(first.agent_id, 1) == first


def test_clone_creates_new_canonical_identity_without_mutating_source() -> None:
    repository, service, _ = stack()
    source = service.create_agent(profile("Source"), owner_ref=OWNER)
    clone = service.clone_agent(source.agent_id, name="Clone")

    assert clone.agent_id != source.agent_id
    assert clone.revision == 1
    assert clone.profile.name == "Clone"
    assert repository.get_agent_revision(source.agent_id, 1).profile.name == "Source"


def test_reference_runtime_pins_exact_revision_even_after_agent_update() -> None:
    repository, service, runtime = stack()
    first = service.create_agent(profile("Pinned v1"), owner_ref=OWNER)
    task_id = new_id("task")
    run_id = new_id("run")

    record = asyncio.run(
        runtime.start_agent(
            task_id=task_id,
            run_id=run_id,
            agent_id=first.agent_id,
        )
    )
    service.update_agent(first.agent_id, profile("Current v2"), expected_revision=1)

    assert record.agent == AgentRevisionRef(agent_id=first.agent_id, revision=1)
    assert record.orchestrator_adapter_id == "reference-orchestrator"
    assert "hermes" not in (record.orchestrator_runtime_ref or "").lower()
    assert repository.get_agent_revision(
        record.agent.agent_id, record.agent.revision
    ).profile.name == ("Pinned v1")
    assert service.get_agent_revision(first.agent_id).profile.name == "Current v2"


class NamedMapper:
    def __init__(self, adapter_id: str) -> None:
        self._adapter_id = adapter_id

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    async def map_agent(self, spec: object) -> OrchestratorMapping:
        agent_spec = spec
        run_id = getattr(agent_spec, "run_id")
        revision = getattr(agent_spec, "agent_revision")
        return OrchestratorMapping(
            adapter_id=self.adapter_id,
            runtime_ref=f"{self.adapter_id}:{revision.agent_id}:{revision.revision}:{run_id}",
        )


def test_same_agent_revision_maps_to_multiple_orchestrator_adapters() -> None:
    _, service, runtime = stack()
    agent = service.create_agent(profile(), owner_ref=OWNER)
    task_id = new_id("task")

    first = asyncio.run(
        runtime.start_agent(
            task_id=task_id,
            run_id=new_id("run"),
            agent_id=agent.agent_id,
            mapper=NamedMapper("orchestrator-a"),
        )
    )
    second = asyncio.run(
        runtime.start_agent(
            task_id=task_id,
            run_id=new_id("run"),
            agent_id=agent.agent_id,
            mapper=NamedMapper("orchestrator-b"),
        )
    )

    assert first.agent == second.agent == AgentRevisionRef(agent_id=agent.agent_id, revision=1)
    assert first.orchestrator_adapter_id == "orchestrator-a"
    assert second.orchestrator_adapter_id == "orchestrator-b"


def test_disabled_agent_fails_before_orchestrator_mapping() -> None:
    _, service, runtime = stack()
    agent = service.create_agent(profile(enabled=False), owner_ref=OWNER)

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            runtime.start_agent(
                task_id=new_id("task"),
                run_id=new_id("run"),
                agent_id=agent.agent_id,
                mapper=NamedMapper("must-not-run"),
            )
        )

    assert exc_info.value.code is ErrorCode.UNAVAILABLE


def test_model_requirements_route_through_canonical_registry_with_explicit_fallback() -> None:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    models = ModelRegistry()
    provider = FakeModelProvider()
    models.register_provider(provider)
    models.register_model(
        ModelConfiguration(
            config_id="model-local-agent",
            display_name="Local Agent Model",
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(
                context_window=32_768,
                tool_calling=True,
                modalities=("text",),
            ),
            location=ModelLocation.LOCAL,
            health=HealthStatus.HEALTHY,
        )
    )
    runtime = AgentRuntime(service, model_registry=models)
    agent = service.create_agent(
        profile(
            model=AgentModelPolicy(
                requirements=RoutingRequirements(
                    explicit_model_id="missing-model",
                    min_context_window=8_000,
                    tool_calling=True,
                    local_only=True,
                ),
                fallback=ModelFallbackPolicy.ROUTE,
            )
        ),
        owner_ref=OWNER,
    )

    record = asyncio.run(
        runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    )

    assert record.selected_model_config_id == "model-local-agent"
    assert record.selected_provider_id == provider.descriptor.provider_id


def test_task_model_override_requires_agent_policy_opt_in() -> None:
    _, service, runtime = stack()
    agent = service.create_agent(profile(), owner_ref=OWNER)

    with pytest.raises(ContractError) as exc_info:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
            task_model_override=RoutingRequirements(tool_calling=True),
        )

    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_registered_required_capability_is_resolved_before_runtime_start() -> None:
    async def scenario() -> None:
        repository = InMemoryAgentRepository()
        service = AgentService(repository)
        registry = CapabilityRegistry()
        await registry.register_provider(NativeEchoProvider())
        runtime = AgentRuntime(service, capability_registry=registry)
        agent = service.create_agent(
            profile(
                capabilities=AgentCapabilityPolicy(
                    allowed=(ECHO_CAPABILITY_ID,),
                    constraints=(
                        CapabilityConstraint(
                            capability_id=ECHO_CAPABILITY_ID,
                            required=True,
                            exact_version="1.0",
                        ),
                    ),
                )
            ),
            owner_ref=OWNER,
        )

        record = await runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
        assert record.capability_ids == (ECHO_CAPABILITY_ID,)

    asyncio.run(scenario())


def test_reviewer_role_has_no_special_bypass_for_denied_capability() -> None:
    _, service, runtime = stack()
    reviewer = service.create_agent(
        profile(
            role="reviewer",
            capabilities=AgentCapabilityPolicy(denied=(ECHO_CAPABILITY_ID,)),
        ),
        owner_ref=OWNER,
    )

    with pytest.raises(ContractError) as exc_info:
        runtime.prepare_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=reviewer.agent_id,
            requested_capability_ids=(ECHO_CAPABILITY_ID,),
            available_capability_ids=frozenset({ECHO_CAPABILITY_ID}),
        )

    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_memory_scope_and_knowledge_assignments_are_explicit_policy_hooks() -> None:
    _, service, _ = stack()
    source_id = new_id("knowledge_source")
    agent = service.create_agent(
        profile(
            data_access=AgentDataAccess(
                memory_scopes=(MemoryScope.AGENT,),
                knowledge_source_ids=(source_id,),
            )
        ),
        owner_ref=OWNER,
    )

    service.ensure_memory_scope(agent.agent_id, 1, MemoryScope.AGENT)
    service.ensure_knowledge_source(agent.agent_id, 1, source_id)
    with pytest.raises(ContractError) as exc_info:
        service.ensure_memory_scope(agent.agent_id, 1, MemoryScope.USER)
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_team_revision_pins_member_revisions_and_skips_only_optional_unavailable_members() -> None:
    repository, service, runtime = stack()
    primary = service.create_agent(profile("Primary"), owner_ref=OWNER)
    optional = service.create_agent(profile("Optional", enabled=False), owner_ref=OWNER)
    team = service.create_team(
        AgentTeamProfile(
            name="Coding team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(primary.agent_id, 1),
                    role="implementer",
                ),
                AgentTeamMember(
                    agent=AgentRevisionRef(optional.agent_id, 1),
                    role="reviewer",
                    required=False,
                ),
            ),
            leader_agent_id=primary.agent_id,
            unavailable_member_policy=UnavailableMemberPolicy.SKIP_OPTIONAL,
        ),
        owner_ref=OWNER,
    )
    service.update_agent(primary.agent_id, profile("Primary v2"), expected_revision=1)

    records = asyncio.run(
        runtime.start_team(
            task_id=new_id("task"),
            run_id=new_id("run"),
            team_id=team.team_id,
            revision=team.revision,
        )
    )

    assert len(records) == 1
    assert records[0].agent == AgentRevisionRef(primary.agent_id, 1)
    assert records[0].team is not None
    assert records[0].team.team_id == team.team_id
    assert records[0].team.revision == 1
    assert repository.get_agent_revision(primary.agent_id, 2).profile.name == "Primary v2"


def test_team_delegation_targets_must_be_members_of_same_team_revision() -> None:
    _, service, _ = stack()
    member = service.create_agent(profile(), owner_ref=OWNER)

    with pytest.raises(ContractError) as exc_info:
        service.create_team(
            AgentTeamProfile(
                name="Invalid team",
                members=(
                    AgentTeamMember(
                        agent=AgentRevisionRef(member.agent_id, 1),
                        role="worker",
                        can_delegate_to=(new_id("agent"),),
                    ),
                ),
            ),
            owner_ref=OWNER,
        )

    assert exc_info.value.code is ErrorCode.INVALID_CONFIGURATION


def test_agent_run_completion_preserves_pinned_revision_and_records_evidence() -> None:
    repository, service, runtime = stack()
    agent = service.create_agent(profile(), owner_ref=OWNER)
    record = asyncio.run(
        runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    )
    artifact_id = new_id("artifact")
    result_id = new_id("result")

    finished = runtime.finish_agent_run(
        record.agent_run_id,
        status=AgentRunStatus.SUCCEEDED,
        artifact_ids=(artifact_id,),
        result_ids=(result_id,),
        model_call_refs=("model-call-1",),
        tool_invocation_refs=("tool-call-1",),
        telemetry={"tokens": 10},
        verification_context={"review_required": False},
    )

    assert finished.agent == record.agent
    assert finished.status is AgentRunStatus.SUCCEEDED
    assert finished.artifact_ids == (artifact_id,)
    assert finished.result_ids == (result_id,)
    assert finished.finished_at is not None
    assert repository.get_agent_run(record.agent_run_id) == finished


def test_active_agent_run_does_not_change_when_definition_is_disabled_later() -> None:
    repository, service, runtime = stack()
    agent = service.create_agent(profile(), owner_ref=OWNER)
    record = asyncio.run(
        runtime.start_agent(
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=agent.agent_id,
        )
    )
    service.update_agent(
        agent.agent_id,
        replace(profile(), enabled=False),
        expected_revision=1,
    )

    persisted = repository.get_agent_run(record.agent_run_id)
    assert persisted.agent.revision == 1
    assert persisted.status is AgentRunStatus.RUNNING
