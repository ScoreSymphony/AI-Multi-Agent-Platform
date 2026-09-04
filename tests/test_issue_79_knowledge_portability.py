from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSearchRequest,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
    new_knowledge_source_id,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.portability import (
    KNOWLEDGE_SOURCE_RESOURCE_TYPE,
    ExclusionCategory,
    IdPolicy,
    ImportContext,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    KnowledgePortableSnapshot,
    KnowledgeSourceImportMutationHandler,
    PackageProvenance,
    ResourceSerializerRegistry,
    build_package,
    knowledge_index_exclusion,
    register_knowledge_portability_codec,
)


def _context(project_id: str | None, user_id: str = "user-a") -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-79-knowledge",
            owner_type="user",
            owner_id=user_id,
            project_id=project_id,
        ),
        actor_ref=f"user:{user_id}",
    )


def _ready_snapshot(
    provider: LocalKnowledgeProvider,
    context: DataAccessContext,
) -> KnowledgePortableSnapshot:
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=context.project_id,
        owner_ref=context.actor_ref,
        created_by=context.actor_ref,
        title="Portable architecture notes",
        revision="r7",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
        metadata={"kind": "notes", "portable": True},
    )
    asyncio.run(provider.register_source(source, context))
    document = asyncio.run(
        provider.ingest_source(
            source.source_id,
            "canonical knowledge content survives provider migration",
            "notes/architecture.md",
            context,
        )
    )
    return KnowledgePortableSnapshot(
        source=replace(
            source,
            status=KnowledgeStatus.READY,
            content_checksum=document.checksum,
        ),
        document=document,
    )


def test_knowledge_source_round_trip_rebuilds_destination_index(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    source_provider = LocalKnowledgeProvider(tmp_path / "source.sqlite")
    snapshot = _ready_snapshot(source_provider, context)
    assert snapshot.document is not None

    serializers = ResourceSerializerRegistry()
    register_knowledge_portability_codec(serializers, id_policy=IdPolicy.REGENERATE)
    resource = serializers.serialize(KNOWLEDGE_SOURCE_RESOURCE_TYPE, snapshot)
    exclusion = knowledge_index_exclusion(snapshot.source.source_id)
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="knowledge-portability-test"),
        excluded_state=(exclusion,),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    target_provider = LocalKnowledgeProvider(tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    mutations.register(KnowledgeSourceImportMutationHandler(target_provider, context))
    result = asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    target_source_id = preview.mapping_dict()[
        (KNOWLEDGE_SOURCE_RESOURCE_TYPE, snapshot.source.source_id)
    ]
    assert target_source_id != snapshot.source.source_id
    assert target_source_id.startswith("knowledge_source_")
    assert result.resources[0].target_id == target_source_id

    index = asyncio.run(target_provider.get_index_status(target_source_id, context))
    assert index.status is KnowledgeStatus.READY
    assert index.revision == snapshot.source.revision
    assert index.index_id.startswith("knowledge_index_")

    hits = asyncio.run(
        target_provider.search(
            KnowledgeSearchRequest(
                query="provider migration",
                context=context,
                source_ids=(target_source_id,),
            )
        )
    )
    assert len(hits) == 1
    assert hits[0].content == snapshot.document.content
    assert hits[0].citation.checksum == snapshot.document.checksum
    assert hits[0].document_id != snapshot.document.document_id

    assert package.manifest.excluded_state == (exclusion,)
    assert exclusion.category is ExclusionCategory.REBUILDABLE_INDEX
    encoded_index = resource.payload["index"]
    assert isinstance(encoded_index, dict)
    assert set(encoded_index) == {"rebuild_required", "source_revision"}


def test_knowledge_source_project_reference_is_deterministically_remapped(tmp_path: Path) -> None:
    source_project = new_id("project")
    target_project = new_id("project")
    source_context = _context(source_project)
    source_provider = LocalKnowledgeProvider(tmp_path / "source.sqlite")
    snapshot = _ready_snapshot(source_provider, source_context)

    serializers = ResourceSerializerRegistry()
    register_knowledge_portability_codec(serializers)
    resource = serializers.serialize(KNOWLEDGE_SOURCE_RESOURCE_TYPE, snapshot)
    decoded = serializers.deserialize(
        resource,
        ImportContext(id_mapping={("project", source_project): target_project}),
    )

    assert isinstance(decoded, KnowledgePortableSnapshot)
    assert decoded.source.project_id == target_project
    assert decoded.document is not None
    assert decoded.document.source_id == decoded.source.source_id


def test_cross_project_knowledge_import_is_rejected_before_mutation(tmp_path: Path) -> None:
    source_project = new_id("project")
    wrong_target_project = new_id("project")
    source_context = _context(source_project)
    source_provider = LocalKnowledgeProvider(tmp_path / "source.sqlite")
    snapshot = _ready_snapshot(source_provider, source_context)

    serializers = ResourceSerializerRegistry()
    register_knowledge_portability_codec(serializers)
    resource = serializers.serialize(KNOWLEDGE_SOURCE_RESOURCE_TYPE, snapshot)
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="knowledge-portability-test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target_provider = LocalKnowledgeProvider(tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    target_context = _context(wrong_target_project)
    mutations.register(KnowledgeSourceImportMutationHandler(target_provider, target_context))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(ContractError) as missing:
        asyncio.run(target_provider.get_index_status(snapshot.source.source_id, target_context))
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_knowledge_source_rollback_removes_active_destination_index(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    source_provider = LocalKnowledgeProvider(tmp_path / "source.sqlite")
    snapshot = _ready_snapshot(source_provider, context)
    serializers = ResourceSerializerRegistry()
    register_knowledge_portability_codec(serializers)
    resource = serializers.serialize(KNOWLEDGE_SOURCE_RESOURCE_TYPE, snapshot)
    decoded = serializers.deserialize(resource)
    assert isinstance(decoded, KnowledgePortableSnapshot)

    target_provider = LocalKnowledgeProvider(tmp_path / "target.sqlite")
    handler = KnowledgeSourceImportMutationHandler(target_provider, context)
    import_context = ImportContext()
    token = asyncio.run(handler.apply(resource, decoded, import_context))
    assert token == snapshot.source.source_id
    index = asyncio.run(target_provider.get_index_status(snapshot.source.source_id, context))
    assert index.status is KnowledgeStatus.READY

    asyncio.run(handler.rollback(resource, decoded, token, import_context))
    with pytest.raises(ContractError) as missing:
        asyncio.run(target_provider.get_index_status(snapshot.source.source_id, context))
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_absolute_filesystem_location_is_not_portable(tmp_path: Path) -> None:
    context = _context(new_id("project"))
    provider = LocalKnowledgeProvider(tmp_path / "source.sqlite")
    snapshot = _ready_snapshot(provider, context)
    assert snapshot.document is not None

    with pytest.raises(ValueError, match="filesystem"):
        KnowledgePortableSnapshot(
            source=snapshot.source,
            document=replace(snapshot.document, location="/srv/private/notes.md"),
        )
