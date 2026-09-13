from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.agents import InMemoryAgentRepository
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ScopeStore
from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Project, Provenance, new_id
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.portability.models import IdPolicy
from ai_multi_agent_platform.portability.package import package_to_dict
from ai_multi_agent_platform.portability.project_codecs import (
    PROJECT_RESOURCE_TYPE,
    ProjectPortableCodec,
)
from ai_multi_agent_platform.portability.project_import import ProjectImportMutationHandler
from ai_multi_agent_platform.portability.registry import ImportContext, ResourceSerializerRegistry
from ai_multi_agent_platform.portability.workflow import ExportSelection


def _project() -> Project:
    return Project(
        id=new_id("project"),
        name="Portable Project",
        owner_ref=OwnerRef(type="user", id="user-owner"),
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        updated_at=datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC),
        schema_version="1.0",
        provenance=Provenance(
            source="migration-test",
            actor_ref="user-owner",
            details={"nested": {"items": [1, "two", True]}, "revision": 7},
        ),
        external_refs=(
            ExternalRef(system="git", kind="repository", value="org/project"),
            ExternalRef(system="catalog", kind="record", value="42"),
        ),
    )


def test_project_codec_preserves_complete_snapshot_and_remaps_identity() -> None:
    source = _project()
    serializers = ResourceSerializerRegistry()
    serializers.register(ProjectPortableCodec(id_policy=IdPolicy.REGENERATE))
    resource = serializers.serialize(PROJECT_RESOURCE_TYPE, source)
    target_id = new_id("project")

    decoded = serializers.deserialize(
        resource,
        ImportContext(id_mapping={(PROJECT_RESOURCE_TYPE, source.id): target_id}),
    )

    assert decoded == replace(source, id=target_id)
    assert resource.id_policy is IdPolicy.REGENERATE


def test_project_round_trip_uses_canonical_scope_store_and_deterministic_remap() -> None:
    source_scopes = ScopeStore()
    source = _project()
    source_scopes.store_project_snapshot(key="source", project=source)
    source_workflow = build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=source_scopes,
        platform_version="0.0.1",
        id_policy=IdPolicy.REGENERATE,
    )
    exported = asyncio.run(
        source_workflow.export_package(
            [ExportSelection(PROJECT_RESOURCE_TYPE, source.id)],
            author="user-owner",
        )
    )

    destination_scopes = ScopeStore()
    destination_workflow = build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=destination_scopes,
        platform_version="0.0.1",
        id_policy=IdPolicy.REGENERATE,
        project_dependency_audit=lambda _project_id: (),
    )
    incoming = destination_workflow.validate_package_document(package_to_dict(exported.package))
    preview = destination_workflow.preview_import(incoming.package_id)

    assert preview.ready is True
    assert preview.preview.resources[0].source_id == source.id
    target_id = preview.preview.resources[0].target_id
    assert target_id != source.id

    report = asyncio.run(destination_workflow.execute_import(preview.preview_id))

    assert report.result.resources[0].target_id == target_id
    assert destination_scopes.get_project(target_id) == replace(source, id=target_id)


def test_preserved_project_identity_conflict_is_reported_before_mutation() -> None:
    source_scopes = ScopeStore()
    source = _project()
    source_scopes.store_project_snapshot(key="source", project=source)
    source_workflow = build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=source_scopes,
        platform_version="0.0.1",
        id_policy=IdPolicy.PRESERVE,
    )
    exported = asyncio.run(
        source_workflow.export_package([ExportSelection(PROJECT_RESOURCE_TYPE, source.id)])
    )

    destination_scopes = ScopeStore()
    destination_scopes.store_project_snapshot(key="existing", project=source)
    destination_workflow = build_agent_portability_workflow(
        agents=InMemoryAgentRepository(),
        models=ModelRegistry(),
        scopes=destination_scopes,
        platform_version="0.0.1",
    )
    incoming = destination_workflow.validate_package_document(package_to_dict(exported.package))
    preview = destination_workflow.preview_import(incoming.package_id)

    assert preview.ready is False
    assert preview.preview.conflicts
    assert destination_scopes.get_project(source.id) == source


def test_project_rollback_requires_cross_domain_safety_proof() -> None:
    scopes = ScopeStore()
    project = _project()
    serializers = ResourceSerializerRegistry()
    serializers.register(ProjectPortableCodec())
    resource = serializers.serialize(PROJECT_RESOURCE_TYPE, project)
    context = ImportContext(id_mapping={(PROJECT_RESOURCE_TYPE, project.id): project.id})
    decoded = serializers.deserialize(resource, context)
    handler = ProjectImportMutationHandler(scopes)

    token = asyncio.run(handler.apply(resource, decoded, context))
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(handler.rollback(resource, decoded, token, context))

    assert exc_info.value.code is ErrorCode.CONFLICT
    assert scopes.get_project(project.id) == project


def test_project_rollback_removes_proven_unreferenced_import() -> None:
    scopes = ScopeStore()
    project = _project()
    serializers = ResourceSerializerRegistry()
    serializers.register(ProjectPortableCodec())
    resource = serializers.serialize(PROJECT_RESOURCE_TYPE, project)
    context = ImportContext(id_mapping={(PROJECT_RESOURCE_TYPE, project.id): project.id})
    decoded = serializers.deserialize(resource, context)
    handler = ProjectImportMutationHandler(scopes, dependency_audit=lambda _project_id: ())

    token = asyncio.run(handler.apply(resource, decoded, context))
    asyncio.run(handler.rollback(resource, decoded, token, context))

    with pytest.raises(ContractError) as exc_info:
        scopes.get_project(project.id)
    assert exc_info.value.code is ErrorCode.NOT_FOUND
