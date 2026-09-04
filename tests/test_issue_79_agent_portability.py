from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents.models import (
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    CapabilityConstraint,
    InstructionSource,
)
from ai_multi_agent_platform.agents.repository import InMemoryAgentRepository
from ai_multi_agent_platform.agents.service import AgentService
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models.types import RoutingRequirements
from ai_multi_agent_platform.portability import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    AgentPortableSnapshot,
    AgentTeamPortableSnapshot,
    DependencyKind,
    IdPolicy,
    ImportConflictKind,
    ImportContext,
    ImportPreviewService,
    PackageProvenance,
    ResourceSerializerRegistry,
    build_package,
    register_agent_portability_codecs,
    snapshot_agent,
    snapshot_agent_team,
)


def _profile(name: str, *, capability: str = "capability.repo.read") -> AgentProfile:
    return AgentProfile(
        name=name,
        role="research",
        instructions=AgentInstructions(role=InstructionSource(content="Inspect canonical state.")),
        model=replace(
            AgentProfile(
                name="base",
                role="base",
                instructions=AgentInstructions(role=InstructionSource(content="base")),
            ).model,
            requirements=RoutingRequirements(explicit_model_id="model.local.default"),
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(capability,),
            constraints=(CapabilityConstraint(capability_id=capability, required=True),),
        ),
    )


def _repository_with_agent_and_team() -> tuple[InMemoryAgentRepository, str, str]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    owner = OwnerRef(type="user", id="portable-test-owner")

    first = service.create_agent(_profile("Researcher v1"), owner_ref=owner)
    second = service.update_agent(first.agent_id, _profile("Researcher v2"))
    team = service.create_team(
        AgentTeamProfile(
            name="Research Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(agent_id=second.agent_id, revision=second.revision),
                    role="researcher",
                ),
            ),
            leader_agent_id=second.agent_id,
            shared_capability_ids=("capability.repo.read",),
        ),
        owner_ref=owner,
    )
    return repository, second.agent_id, team.team_id


def test_agent_and_team_revision_histories_round_trip_without_runtime_state() -> None:
    repository, agent_id, team_id = _repository_with_agent_and_team()
    registry = ResourceSerializerRegistry()
    register_agent_portability_codecs(registry)

    agent_resource = registry.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(repository, agent_id))
    team_resource = registry.serialize(
        AGENT_TEAM_RESOURCE_TYPE,
        snapshot_agent_team(repository, team_id),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(agent_resource, team_resource),
        provenance=PackageProvenance(source="test"),
    )

    restored_agent = registry.deserialize(package.resources[0])
    restored_team = registry.deserialize(package.resources[1])

    assert isinstance(restored_agent, AgentPortableSnapshot)
    assert isinstance(restored_team, AgentTeamPortableSnapshot)
    assert restored_agent == snapshot_agent(repository, agent_id)
    assert restored_team == snapshot_agent_team(repository, team_id)
    assert restored_agent.definition.current_revision == 2
    assert len(restored_agent.revisions) == 2
    assert "orchestrator_runtime_ref" not in str(package.resources)


def test_composite_agent_team_preview_orders_dependency_and_remaps_references() -> None:
    repository, agent_id, team_id = _repository_with_agent_and_team()
    registry = ResourceSerializerRegistry()
    register_agent_portability_codecs(
        registry,
        agent_id_policy=IdPolicy.REGENERATE,
        team_id_policy=IdPolicy.REGENERATE,
    )
    agent_resource = registry.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(repository, agent_id))
    team_resource = registry.serialize(
        AGENT_TEAM_RESOURCE_TYPE,
        snapshot_agent_team(repository, team_id),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(team_resource, agent_resource),
        provenance=PackageProvenance(source="test"),
    )

    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    assert preview.ready is True
    assert preview.import_order == ((AGENT_RESOURCE_TYPE, agent_id), (AGENT_TEAM_RESOURCE_TYPE, team_id))
    mapping = preview.mapping_dict()
    assert mapping[(AGENT_RESOURCE_TYPE, agent_id)] != agent_id
    assert mapping[(AGENT_TEAM_RESOURCE_TYPE, team_id)] != team_id

    context = ImportContext(id_mapping=mapping)
    imported_agent = registry.deserialize(agent_resource, context)
    imported_team = registry.deserialize(team_resource, context)
    assert isinstance(imported_agent, AgentPortableSnapshot)
    assert isinstance(imported_team, AgentTeamPortableSnapshot)
    assert imported_agent.definition.agent_id == mapping[(AGENT_RESOURCE_TYPE, agent_id)]
    member = imported_team.revisions[-1].profile.members[0]
    assert member.agent.agent_id == imported_agent.definition.agent_id
    assert imported_team.revisions[-1].profile.leader_agent_id == imported_agent.definition.agent_id


def test_preview_reports_existing_id_conflict_before_any_mutation() -> None:
    repository, agent_id, _ = _repository_with_agent_and_team()
    registry = ResourceSerializerRegistry()
    register_agent_portability_codecs(registry)
    resource = registry.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(repository, agent_id))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )
    calls: list[tuple[str, str]] = []

    def resource_exists(resource_type: str, resource_id: str) -> bool:
        calls.append((resource_type, resource_id))
        return resource_type == AGENT_RESOURCE_TYPE and resource_id == agent_id

    preview = ImportPreviewService(
        resource_exists=resource_exists,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    assert calls
    assert preview.ready is False
    assert preview.conflicts[0].kind is ImportConflictKind.ID_EXISTS
    assert preview.conflicts[0].resource_id == agent_id


def test_preview_reports_required_missing_capability_dependency() -> None:
    repository, agent_id, _ = _repository_with_agent_and_team()
    registry = ResourceSerializerRegistry()
    register_agent_portability_codecs(registry)
    resource = registry.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(repository, agent_id))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )

    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda requirement: requirement.kind is not DependencyKind.CAPABILITY,
    ).preview(package)

    assert preview.ready is False
    assert preview.missing_dependencies
    assert all(
        item.requirement.kind is DependencyKind.CAPABILITY
        for item in preview.missing_dependencies
    )
    assert any(
        item.requirement.identifier == "capability.repo.read"
        for item in preview.missing_dependencies
    )


def test_preview_reports_name_conflict_independently_from_id_conflict() -> None:
    repository, agent_id, _ = _repository_with_agent_and_team()
    registry = ResourceSerializerRegistry()
    register_agent_portability_codecs(registry)
    resource = registry.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(repository, agent_id))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )

    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
        name_conflict=lambda _resource: "target already contains Agent named Researcher v2",
    ).preview(package)

    assert preview.ready is False
    assert len(preview.conflicts) == 1
    assert preview.conflicts[0].kind is ImportConflictKind.NAME_EXISTS
