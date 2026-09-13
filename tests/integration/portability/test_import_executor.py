from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.agents.models import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentTeamMember,
    AgentTeamProfile,
    InstructionSource,
)
from ai_multi_agent_platform.agents.repository import InMemoryAgentRepository
from ai_multi_agent_platform.agents.service import AgentService
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import DataAccessContext, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.portability import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    FILE_RESOURCE_TYPE,
    AgentImportMutationHandler,
    AgentTeamImportMutationHandler,
    FileImportMutationHandler,
    IdPolicy,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    PackageProvenance,
    PortablePackage,
    PortableResource,
    ResourceExport,
    ResourceSerializerRegistry,
    build_package,
    register_agent_portability_codecs,
    register_file_portability_codecs,
    resource_dependency,
    snapshot_agent,
    snapshot_agent_team,
    snapshot_file,
)
from ai_multi_agent_platform.portability.registry import ImportContext


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="portable-test",
        instructions=AgentInstructions(role=InstructionSource(content="Portable test agent.")),
    )


def _source_agents() -> tuple[InMemoryAgentRepository, str, str]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    owner = OwnerRef(type="user", id="import-owner")
    first = service.create_agent(_agent_profile("Agent v1"), owner_ref=owner)
    second = service.update_agent(first.agent_id, _agent_profile("Agent v2"))
    team = service.create_team(
        AgentTeamProfile(
            name="Portable Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(second.agent_id, second.revision),
                    role="worker",
                ),
            ),
            leader_agent_id=second.agent_id,
        ),
        owner_ref=owner,
    )
    return repository, second.agent_id, team.team_id


def _agent_package(
    source: InMemoryAgentRepository,
    agent_id: str,
    team_id: str,
) -> tuple[ResourceSerializerRegistry, PortablePackage]:
    serializers = ResourceSerializerRegistry()
    register_agent_portability_codecs(
        serializers,
        agent_id_policy=IdPolicy.REGENERATE,
        team_id_policy=IdPolicy.REGENERATE,
    )
    agent = serializers.serialize(AGENT_RESOURCE_TYPE, snapshot_agent(source, agent_id))
    team = serializers.serialize(AGENT_TEAM_RESOURCE_TYPE, snapshot_agent_team(source, team_id))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(team, agent),
        provenance=PackageProvenance(source="test"),
    )
    return serializers, package


def test_executor_imports_agent_then_team_with_full_revision_history() -> None:
    source, agent_id, team_id = _source_agents()
    serializers, package = _agent_package(source, agent_id, team_id)

    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = InMemoryAgentRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(target))
    mutations.register(AgentTeamImportMutationHandler(target))

    result = asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))
    mapping = preview.mapping_dict()
    imported_agent_id = mapping[(AGENT_RESOURCE_TYPE, agent_id)]
    imported_team_id = mapping[(AGENT_TEAM_RESOURCE_TYPE, team_id)]

    assert result.package_checksum == package.checksum
    assert target.get_agent(imported_agent_id).current_revision == 2
    assert len(target.list_agent_revisions(imported_agent_id)) == 2
    imported_team = target.get_team_revision(imported_team_id, 1)
    assert imported_team.profile.members[0].agent.agent_id == imported_agent_id


class _FailCodec:
    resource_type = "fail"

    def __init__(self, dependency_type: str, dependency_id: str) -> None:
        self._dependency_type = dependency_type
        self._dependency_id = dependency_id

    def serialize(self, value: object) -> ResourceExport:
        del value
        return ResourceExport(
            resource_id="fail-1",
            resource_version="1",
            payload={"message": "fail after dependencies"},
            dependencies=(
                resource_dependency(
                    self._dependency_type,
                    self._dependency_id,
                    purpose="test failure ordering",
                ),
            ),
        )

    def deserialize(self, resource: PortableResource, context: ImportContext) -> object:
        del context
        return resource.payload["message"]


class _FailMutationHandler:
    resource_type = "fail"

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, value, context
        raise ContractError(ErrorCode.BACKEND_ERROR, "simulated package import failure")

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, token, context


def test_executor_rolls_back_real_team_and_agent_in_reverse_order() -> None:
    source, agent_id, team_id = _source_agents()
    serializers, package = _agent_package(source, agent_id, team_id)
    serializers.register(_FailCodec(AGENT_TEAM_RESOURCE_TYPE, team_id))
    fail_resource = serializers.serialize("fail", object())
    package = build_package(
        source_platform_version="0.0.1",
        resources=(*package.resources, fail_resource),
        provenance=PackageProvenance(source="test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    target = InMemoryAgentRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(target))
    mutations.register(AgentTeamImportMutationHandler(target))
    mutations.register(_FailMutationHandler())

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.BACKEND_ERROR
    assert failed.value.details["rollback_complete"] is True
    mapping = preview.mapping_dict()
    with pytest.raises(ContractError) as agent_missing:
        target.get_agent(mapping[(AGENT_RESOURCE_TYPE, agent_id)])
    with pytest.raises(ContractError) as team_missing:
        target.get_team(mapping[(AGENT_TEAM_RESOURCE_TYPE, team_id)])
    assert agent_missing.value.code is ErrorCode.NOT_FOUND
    assert team_missing.value.code is ErrorCode.NOT_FOUND


def test_executor_rejects_not_ready_preview_before_mutation() -> None:
    source, agent_id, team_id = _source_agents()
    serializers, package = _agent_package(source, agent_id, team_id)
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    blocked = replace(preview, ready=False)
    target = InMemoryAgentRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(target))
    mutations.register(AgentTeamImportMutationHandler(target))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, blocked))

    assert failed.value.code is ErrorCode.CONFLICT
    assert target.list_agents() == ()
    assert target.list_teams() == ()


def test_executor_resolves_all_handlers_before_first_mutation() -> None:
    source, agent_id, team_id = _source_agents()
    serializers, package = _agent_package(source, agent_id, team_id)
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = InMemoryAgentRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AgentImportMutationHandler(target))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.NOT_FOUND
    assert target.list_agents() == ()


def _file_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-79-executor-file",
            owner_type="user",
            owner_id="import-owner",
            project_id=project_id,
        ),
        actor_ref="user:import-owner",
    )


def test_package_rollback_removes_file_written_by_destination_provider(tmp_path: Path) -> None:
    project_id = new_id("project")
    data_context = _file_context(project_id)
    source = LocalFileProvider(tmp_path / "source", tmp_path / "source.sqlite")
    created = asyncio.run(source.create_file(b"package rollback bytes", data_context))

    serializers = ResourceSerializerRegistry()
    register_file_portability_codecs(serializers, file_id_policy=IdPolicy.REGENERATE)
    file_resource = serializers.serialize(
        FILE_RESOURCE_TYPE,
        asyncio.run(snapshot_file(source, created.file_id, data_context)),
    )
    serializers.register(_FailCodec(FILE_RESOURCE_TYPE, created.file_id))
    fail_resource = serializers.serialize("fail", object())
    package = build_package(
        source_platform_version="0.0.1",
        resources=(fail_resource, file_resource),
        provenance=PackageProvenance(source="test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    target = LocalFileProvider(tmp_path / "target", tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    mutations.register(FileImportMutationHandler(target, data_context))
    mutations.register(_FailMutationHandler())

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.details["rollback_complete"] is True
    imported_file_id = preview.mapping_dict()[(FILE_RESOURCE_TYPE, created.file_id)]
    with pytest.raises(ContractError) as missing:
        asyncio.run(target.get_file(imported_file_id, data_context))
    assert missing.value.code is ErrorCode.NOT_FOUND
