from __future__ import annotations

import asyncio
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
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.data.reference import (
    LocalKnowledgeProvider as PreLifecycleKnowledgeProvider,
)
from ai_multi_agent_platform.data.reference import LocalMemoryProvider as PreLifecycleMemoryProvider
from ai_multi_agent_platform.domain import new_id


def _context(
    *,
    owner_type: str = "user",
    owner_id: str = "user-a",
    project_id: str | None = None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-251-reference",
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref=f"{owner_type}:{owner_id}",
    )


def _memory_entry(
    *,
    scope: MemoryScope,
    scope_id: str,
    owner_ref: str,
    origin: MemoryOrigin,
) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref=owner_ref,
        created_by=owner_ref,
        value={"fact": "persisted", "origin": origin.value},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        origin=origin,
    )


def test_memory_origin_migrates_existing_database_and_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    context = _context()

    pre_lifecycle = PreLifecycleMemoryProvider(db_path)
    old_entry = _memory_entry(
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        origin=MemoryOrigin.USER_AUTHORED,
    )
    asyncio.run(pre_lifecycle.write_entry(old_entry, context))

    migrated = LocalMemoryProvider(db_path)
    assert (
        asyncio.run(migrated.get_entry(old_entry.memory_id, context)).origin
        is MemoryOrigin.USER_AUTHORED
    )

    imported = _memory_entry(
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        origin=MemoryOrigin.IMPORTED,
    )
    asyncio.run(migrated.write_entry(imported, context))

    restarted = LocalMemoryProvider(db_path)
    restored = asyncio.run(restarted.get_entry(imported.memory_id, context))
    assert restored.origin is MemoryOrigin.IMPORTED
    assert "seven_scopes" in restarted.descriptor.capabilities[0].features
    assert "memory_origin" in restarted.descriptor.capabilities[0].features
    assert "six_scopes" not in restarted.descriptor.capabilities[0].features


def test_organization_memory_rejects_mismatched_organization_owner(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    organization_id = "org:alpha"
    entry = _memory_entry(
        scope=MemoryScope.ORGANIZATION,
        scope_id=organization_id,
        owner_ref=f"organization:{organization_id}",
        origin=MemoryOrigin.IMPORTED,
    )
    allowed = _context(owner_type="organization", owner_id=organization_id)
    asyncio.run(provider.write_entry(entry, allowed))

    denied = _context(owner_type="organization", owner_id="org:beta")
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(entry.memory_id, denied))
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_local_knowledge_provider_exposes_canonical_source_discovery(tmp_path: Path) -> None:
    provider = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    project_id = new_id("project")
    context = _context(project_id=project_id)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Issue 251 source",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )

    asyncio.run(provider.register_source(source, context))

    assert asyncio.run(provider.get_source(source.source_id, context)) == source
    assert asyncio.run(provider.list_sources(context)) == (source,)
    assert "get_source" in provider.descriptor.supported_operations
    assert "list_sources" in provider.descriptor.supported_operations
    assert "source_discovery" in provider.descriptor.capabilities[0].features


def test_knowledge_source_revision_and_query_survive_provider_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "knowledge.sqlite3"
    project_id = new_id("project")
    context = _context(project_id=project_id)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Restart-persistent source",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
        metadata={"classification": "reference", "owner": "issue-251"},
    )

    provider = LocalKnowledgeProvider(db_path)
    asyncio.run(provider.register_source(source, context))
    asyncio.run(provider.ingest_source(source.source_id, "initial revision", "section:r1", context))
    reindexed = asyncio.run(
        provider.reindex_source(
            source.source_id,
            "r2",
            "restart persistence remains queryable",
            "section:restart",
            context,
        )
    )

    restarted = LocalKnowledgeProvider(db_path)
    restored_source = asyncio.run(restarted.get_source(source.source_id, context))
    assert restored_source.source_id == source.source_id
    assert restored_source.project_id == source.project_id
    assert restored_source.owner_ref == source.owner_ref
    assert restored_source.title == source.title
    assert restored_source.revision == "r2"
    assert restored_source.status is KnowledgeStatus.READY
    assert restored_source.metadata == source.metadata

    restored_index = asyncio.run(restarted.get_index_status(source.source_id, context))
    assert restored_index.source_id == source.source_id
    assert restored_index.revision == "r2"
    assert restored_index.status is KnowledgeStatus.READY

    results = asyncio.run(
        restarted.search(
            KnowledgeSearchRequest(
                query="restart persistence",
                context=context,
                source_ids=(source.source_id,),
            )
        )
    )
    assert len(results) == 1
    result = results[0]
    assert result.source_id == source.source_id
    assert result.document_id == reindexed.document_id
    assert result.revision == "r2"
    assert result.content == "restart persistence remains queryable"
    assert result.location == "section:restart"
    assert result.citation.ref == reindexed.document_id
    assert result.citation.revision == "r2"
    assert result.citation.location == "section:restart"
    assert result.citation.checksum == reindexed.checksum


def test_knowledge_source_discovery_preserves_project_isolation(tmp_path: Path) -> None:
    provider = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    project_a = new_id("project")
    project_b = new_id("project")
    context_a = _context(project_id=project_a)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_a,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Private project source",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    asyncio.run(provider.register_source(source, context_a))

    context_b = _context(project_id=project_b)
    assert asyncio.run(provider.list_sources(context_b)) == ()
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_source(source.source_id, context_b))
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_knowledge_reindex_failure_preserves_source_metadata_and_marks_failed(
    tmp_path: Path,
) -> None:
    class FailingIngestProvider(LocalKnowledgeProvider):
        async def ingest_source(
            self,
            source_id: str,
            content: str,
            location: str,
            context: DataAccessContext,
        ):
            del source_id, content, location, context
            raise ContractError(ErrorCode.BACKEND_ERROR, "simulated index backend failure")

    provider = FailingIngestProvider(tmp_path / "knowledge.sqlite3")
    project_id = new_id("project")
    context = _context(project_id=project_id)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Durable source metadata",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
        metadata={"classification": "reference"},
    )
    asyncio.run(provider.register_source(source, context))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            provider.reindex_source(
                source.source_id,
                "r2",
                "content that fails to index",
                "section:failed",
                context,
            )
        )
    assert exc_info.value.code is ErrorCode.BACKEND_ERROR

    failed_source = asyncio.run(provider.get_source(source.source_id, context))
    assert failed_source.source_id == source.source_id
    assert failed_source.title == source.title
    assert failed_source.metadata == source.metadata
    assert failed_source.revision == "r2"
    assert failed_source.status is KnowledgeStatus.FAILED

    failed_index = asyncio.run(provider.get_index_status(source.source_id, context))
    assert failed_index.source_id == source.source_id
    assert failed_index.revision == "r2"
    assert failed_index.status is KnowledgeStatus.FAILED
    assert "explicit_failure_state" in provider.descriptor.capabilities[0].features


def test_pre_lifecycle_knowledge_provider_degrades_explicitly(tmp_path: Path) -> None:
    provider = PreLifecycleKnowledgeProvider(tmp_path / "knowledge.sqlite3")

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.list_sources(_context()))
    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
