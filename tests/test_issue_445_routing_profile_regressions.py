from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.models import (
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    ModelRoutingProfileService,
    new_model_routing_profile_id,
)
from ai_multi_agent_platform.models.routing_profile_assignment_context import (
    RoutingProfileAssignmentAccess,
    activate_routing_profile_assignment_access,
)
from ai_multi_agent_platform.portability import (
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    PackageProvenance,
    PortableResource,
    ResourceSerializerRegistry,
    build_package,
    seal_resource,
)
from ai_multi_agent_platform.portability.agent_codecs import snapshot_agent
from ai_multi_agent_platform.portability.agent_import import AgentImportMutationHandler
from ai_multi_agent_platform.portability.model_routing_profile_codecs import (
    MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
    ModelRoutingProfilePortableCodec,
    snapshot_model_routing_profile,
)
from ai_multi_agent_platform.portability.model_routing_profile_import import (
    ModelRoutingProfileImportMutationHandler,
)
from ai_multi_agent_platform.portability.registry import ImportContext
from ai_multi_agent_platform.portability.routing_profile_reference_codecs import (
    RoutingProfileAwareAgentPortableCodec,
)
from ai_multi_agent_platform.testing import FakeAuthorizationProvider

OWNER = OwnerRef(type="user", id="user-issue-445-regressions")


def _operation(project_id: str | None = None) -> OperationContext:
    return OperationContext(
        correlation_id="corr-issue-445-regressions",
        owner_type=OWNER.type,
        owner_id=OWNER.id,
        project_id=project_id,
    )


def _agent_profile(routing_profile_ref: str) -> AgentProfile:
    return AgentProfile(
        name="Issue 445 regression Agent",
        role="researcher",
        instructions=AgentInstructions(
            role=InstructionSource(content="Exercise routing-profile import invariants.")
        ),
        model=AgentModelPolicy(routing_profile_ref=routing_profile_ref),
    )


def _create_profile(
    repository: JsonModelRoutingProfileRepository,
    *,
    project_id: str | None = None,
    profile_id: str | None = None,
) -> ModelRoutingProfileRevision:
    return asyncio.run(
        ModelRoutingProfileService(repository).create_profile(
            name="Issue 445 regression profile",
            policy=ModelRoutingProfilePolicy(),
            owner_ref=OWNER,
            principal_ref=OWNER.id,
            context=_operation(project_id),
            project_id=project_id,
            profile_id=profile_id,
        )
    )


def _serialized_agent(
    routing_profile_ref: str,
    *,
    project_id: str | None = None,
) -> tuple[AgentService, ResourceSerializerRegistry, PortableResource]:
    source_agents = AgentService(InMemoryAgentRepository())
    revision = source_agents.create_agent(
        _agent_profile(routing_profile_ref),
        owner_ref=OWNER,
        project_id=project_id,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareAgentPortableCodec())
    resource = serializers.serialize(
        "agent",
        snapshot_agent(source_agents.repository, revision.agent_id),
    )
    return source_agents, serializers, resource


def test_portable_agent_import_rejects_malformed_canonical_reference_before_mutation() -> None:
    _, serializers, resource = _serialized_agent("model_routing_profile_broken@rX")
    snapshot = serializers.deserialize(resource, ImportContext())
    target = InMemoryAgentRepository()

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            AgentImportMutationHandler(target).preflight(
                resource,
                snapshot,
                ImportContext(),
            )
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert target.list_agents() == ()


def test_portable_agent_import_rejects_undeclared_canonical_dependency_before_mutation() -> None:
    routing_ref = ModelRoutingProfileRef(new_model_routing_profile_id(), 1).canonical_ref
    _, serializers, resource = _serialized_agent(routing_ref)
    resource = seal_resource(replace(resource, dependencies=(), checksum=""))
    snapshot = serializers.deserialize(resource, ImportContext())
    target = InMemoryAgentRepository()

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            AgentImportMutationHandler(target).preflight(
                resource,
                snapshot,
                ImportContext(),
            )
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert target.list_agents() == ()


def test_portable_agent_import_checks_canonical_dependencies_across_immutable_history() -> None:
    first_ref = ModelRoutingProfileRef(new_model_routing_profile_id(), 1).canonical_ref
    second_profile_id = new_model_routing_profile_id()
    second_ref = ModelRoutingProfileRef(second_profile_id, 2).canonical_ref
    source_agents = AgentService(InMemoryAgentRepository())
    first = source_agents.create_agent(_agent_profile(first_ref), owner_ref=OWNER)
    source_agents.update_agent(
        first.agent_id,
        _agent_profile(second_ref),
        expected_revision=1,
    )
    serializers = ResourceSerializerRegistry()
    serializers.register(RoutingProfileAwareAgentPortableCodec())
    resource = serializers.serialize(
        "agent",
        snapshot_agent(source_agents.repository, first.agent_id),
    )
    resource = seal_resource(
        replace(
            resource,
            dependencies=tuple(
                dependency
                for dependency in resource.dependencies
                if second_profile_id not in dependency.identifier
            ),
            checksum="",
        )
    )
    snapshot = serializers.deserialize(resource, ImportContext())
    target = InMemoryAgentRepository()

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            AgentImportMutationHandler(target).preflight(
                resource,
                snapshot,
                ImportContext(),
            )
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
    assert target.list_agents() == ()


def test_pre_309_legacy_routing_key_remains_compatibility_data() -> None:
    legacy_key = "legacy-router-key"
    _, serializers, resource = _serialized_agent(legacy_key)
    snapshot = serializers.deserialize(resource, ImportContext())
    target = InMemoryAgentRepository()
    handler = AgentImportMutationHandler(target)

    asyncio.run(handler.preflight(resource, snapshot, ImportContext()))
    token = asyncio.run(handler.apply(resource, snapshot, ImportContext()))

    imported = target.get_agent_revision(token, 1)
    assert imported.profile.model.routing_profile_ref == legacy_key


def test_portable_agent_assignment_uses_imported_target_project_scope(tmp_path) -> None:
    project_id = new_id("project")
    profiles = JsonModelRoutingProfileRepository(tmp_path / "profiles.json")
    routing_profile = _create_profile(profiles, project_id=project_id)
    _, serializers, resource = _serialized_agent(
        routing_profile.ref.canonical_ref,
        project_id=project_id,
    )
    snapshot = serializers.deserialize(resource, ImportContext())
    authorization = FakeAuthorizationProvider(allowed=True)
    target = InMemoryAgentRepository()
    access = RoutingProfileAssignmentAccess(
        gate=ModelRoutingProfileAssignmentGate(
            profiles,
            authorization=authorization,
        ),
        principal_ref=OWNER.id,
        actor_type="human",
        correlation_id="corr-issue-445-target-scope",
        causation_id="request-issue-445-target-scope",
    )
    handler = AgentImportMutationHandler(target)

    with activate_routing_profile_assignment_access(access):
        asyncio.run(handler.preflight(resource, snapshot, ImportContext()))
        asyncio.run(handler.apply(resource, snapshot, ImportContext()))

    call = authorization.calls[-1]
    assert call.action == "model-routing-profile:assign"
    assert call.context.project_id == project_id
    assert call.context.owner_type == OWNER.type
    assert call.context.owner_id == OWNER.id


def test_assignment_denial_rolls_back_earlier_in_package_routing_profile_import(
    tmp_path,
) -> None:
    source_profiles = JsonModelRoutingProfileRepository(tmp_path / "source-profiles.json")
    routing_profile = _create_profile(source_profiles)
    _, serializers, agent_resource = _serialized_agent(routing_profile.ref.canonical_ref)
    serializers.register(ModelRoutingProfilePortableCodec())
    profile_resource = serializers.serialize(
        MODEL_ROUTING_PROFILE_RESOURCE_TYPE,
        snapshot_model_routing_profile(source_profiles, routing_profile.profile_id),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(profile_resource, agent_resource),
        provenance=PackageProvenance(source="issue-445-regression-test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    target_profiles = JsonModelRoutingProfileRepository(tmp_path / "target-profiles.json")
    target_agents = InMemoryAgentRepository()
    mutations = ImportMutationRegistry()
    mutations.register(ModelRoutingProfileImportMutationHandler(target_profiles))
    mutations.register(AgentImportMutationHandler(target_agents))
    authorization = FakeAuthorizationProvider(allowed=False)
    access = RoutingProfileAssignmentAccess(
        gate=ModelRoutingProfileAssignmentGate(
            target_profiles,
            authorization=authorization,
        ),
        principal_ref=OWNER.id,
        actor_type="human",
        correlation_id="corr-issue-445-package-rollback",
        causation_id="request-issue-445-package-rollback",
    )

    with activate_routing_profile_assignment_access(access):
        with pytest.raises(ContractError) as caught:
            asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert caught.value.code is ErrorCode.FORBIDDEN
    assert target_agents.list_agents() == ()
    with pytest.raises(ContractError) as missing:
        target_profiles.get_definition(routing_profile.profile_id)
    assert missing.value.code is ErrorCode.NOT_FOUND
