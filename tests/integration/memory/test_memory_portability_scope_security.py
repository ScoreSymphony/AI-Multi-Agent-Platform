from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.portability import (
    MEMORY_RESOURCE_TYPE,
    IdPolicy,
    ImportContext,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    MemoryImportMutationHandler,
    MemoryPortableSnapshot,
    PackageProvenance,
    ResourceSerializerRegistry,
    build_package,
    register_memory_portability_codec,
    snapshot_memory,
)


def _context(project_id: str | None, user_id: str = "user-a") -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-79-memory",
            owner_type="user",
            owner_id=user_id,
            project_id=project_id,
        ),
        actor_ref=f"user:{user_id}",
    )


def _workspace_entry(project_id: str) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.WORKSPACE,
        scope_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"portable": True, "project": project_id},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.PROJECT_LIFETIME,
        provenance=(
            SourceRef(
                kind="document",
                ref="portable-source",
                revision="v1",
            ),
        ),
        classification="internal",
        metadata={"purpose": "issue-79-memory-privacy"},
    )


def _user_entry(user_id: str = "user-a") -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id=user_id,
        owner_ref=f"user:{user_id}",
        created_by=f"user:{user_id}",
        value={"preference": "private"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
    )


def test_workspace_memory_round_trip_preserves_content_provenance_and_privacy_scope(
    tmp_path: Path,
) -> None:
    project_id = new_id("project")
    source_context = _context(project_id)
    source = LocalMemoryProvider(tmp_path / "source.sqlite")
    entry = _workspace_entry(project_id)
    asyncio.run(source.write_entry(entry, source_context))

    serializers = ResourceSerializerRegistry()
    register_memory_portability_codec(serializers, id_policy=IdPolicy.REGENERATE)
    resource = serializers.serialize(
        MEMORY_RESOURCE_TYPE,
        asyncio.run(snapshot_memory(source, entry.memory_id, source_context)),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)

    target = LocalMemoryProvider(tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    mutations.register(MemoryImportMutationHandler(target, source_context))
    result = asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    imported_id = preview.mapping_dict()[(MEMORY_RESOURCE_TYPE, entry.memory_id)]
    imported = asyncio.run(target.get_entry(imported_id, source_context))
    assert result.resources[0].target_id == imported_id
    assert imported.memory_id != entry.memory_id
    assert imported.scope is MemoryScope.WORKSPACE
    assert imported.scope_id == project_id
    assert imported.value == entry.value
    assert imported.provenance == entry.provenance
    assert imported.classification == "internal"
    assert imported.metadata == entry.metadata


def test_workspace_memory_project_reference_can_be_deterministically_remapped(
    tmp_path: Path,
) -> None:
    source_project = new_id("project")
    target_project = new_id("project")
    source_context = _context(source_project)
    source = LocalMemoryProvider(tmp_path / "source.sqlite")
    entry = _workspace_entry(source_project)
    asyncio.run(source.write_entry(entry, source_context))

    serializers = ResourceSerializerRegistry()
    register_memory_portability_codec(serializers, id_policy=IdPolicy.REGENERATE)
    resource = serializers.serialize(
        MEMORY_RESOURCE_TYPE,
        asyncio.run(snapshot_memory(source, entry.memory_id, source_context)),
    )
    target_memory_id = new_memory_id()
    imported = serializers.deserialize(
        resource,
        ImportContext(
            id_mapping={
                (MEMORY_RESOURCE_TYPE, entry.memory_id): target_memory_id,
                ("project", source_project): target_project,
            }
        ),
    )

    assert isinstance(imported, MemoryPortableSnapshot)
    assert imported.entry.memory_id == target_memory_id
    assert imported.entry.scope_id == target_project
    assert imported.source_project_id == target_project


def test_cross_project_workspace_memory_is_rejected_before_mutation(tmp_path: Path) -> None:
    source_project = new_id("project")
    target_project = new_id("project")
    source_context = _context(source_project)
    target_context = _context(target_project)
    source = LocalMemoryProvider(tmp_path / "source.sqlite")
    entry = _workspace_entry(source_project)
    asyncio.run(source.write_entry(entry, source_context))

    serializers = ResourceSerializerRegistry()
    register_memory_portability_codec(serializers, id_policy=IdPolicy.REGENERATE)
    resource = serializers.serialize(
        MEMORY_RESOURCE_TYPE,
        asyncio.run(snapshot_memory(source, entry.memory_id, source_context)),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = LocalMemoryProvider(tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    mutations.register(MemoryImportMutationHandler(target, target_context))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.FORBIDDEN
    imported_id = preview.mapping_dict()[(MEMORY_RESOURCE_TYPE, entry.memory_id)]
    with pytest.raises(ContractError) as missing:
        asyncio.run(target.get_entry(imported_id, target_context))
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_user_memory_cannot_be_transferred_to_another_user_implicitly(tmp_path: Path) -> None:
    source_context = _context(None, "user-a")
    target_context = _context(None, "user-b")
    source = LocalMemoryProvider(tmp_path / "source.sqlite")
    entry = _user_entry("user-a")
    asyncio.run(source.write_entry(entry, source_context))

    serializers = ResourceSerializerRegistry()
    register_memory_portability_codec(serializers, id_policy=IdPolicy.REGENERATE)
    resource = serializers.serialize(
        MEMORY_RESOURCE_TYPE,
        asyncio.run(snapshot_memory(source, entry.memory_id, source_context)),
    )
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = LocalMemoryProvider(tmp_path / "target.sqlite")
    mutations = ImportMutationRegistry()
    mutations.register(MemoryImportMutationHandler(target, target_context))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(serializers, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.FORBIDDEN


def test_short_term_execution_memory_is_never_portable(tmp_path: Path) -> None:
    context = _context(None)
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite")
    now = datetime.now(UTC)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.SHORT_TERM,
        scope_id="execution-session-1",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"session": "runtime-only"},
        created_at=now,
        retention=RetentionPolicy.EPHEMERAL,
        expires_at=now + timedelta(minutes=30),
    )
    asyncio.run(provider.write_entry(entry, context))

    with pytest.raises(ContractError) as failed:
        asyncio.run(snapshot_memory(provider, entry.memory_id, context))

    assert failed.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
