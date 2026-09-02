from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    FileState,
    KnowledgeSearchMode,
    KnowledgeSearchRequest,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


def _operation(project_id: str | None = None, owner_id: str = "user-a") -> OperationContext:
    return OperationContext(
        correlation_id="corr-test",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )


def _context(project_id: str | None = None, owner_id: str = "user-a") -> DataAccessContext:
    operation = _operation(project_id, owner_id)
    return DataAccessContext(operation=operation, actor_ref=f"user:{owner_id}")


def test_file_create_read_stream_link_and_delete(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalFileProvider(tmp_path / "objects", tmp_path / "data.sqlite")

    record = asyncio.run(provider.create_file(b"hello world", context, content_type="text/plain"))
    assert record.file_id.startswith("file_")
    assert record.state is FileState.READY
    assert asyncio.run(provider.read(record.file_id, context.operation)) == b"hello world"
    assert asyncio.run(provider.verify_checksum(record.file_id, context))

    async def collect() -> bytes:
        chunks = [
            chunk async for chunk in provider.stream_file(record.file_id, context, chunk_size=3)
        ]
        return b"".join(chunks)

    assert asyncio.run(collect()) == b"hello world"

    artifact_id = new_id("artifact")
    linked = asyncio.run(provider.link_artifact(record.file_id, artifact_id, context))
    assert linked.artifact_ids == (artifact_id,)

    deleted = asyncio.run(provider.delete_file(record.file_id, context))
    assert deleted.state is FileState.TOMBSTONED
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.read(record.file_id, context.operation))
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_file_checksum_failure_and_orphan_detection(tmp_path: Path) -> None:
    context = _context(new_id("project"))
    root = tmp_path / "objects"
    provider = LocalFileProvider(root, tmp_path / "data.sqlite")
    record = asyncio.run(provider.create_file(b"original", context))

    (root / record.file_id).write_bytes(b"tampered")
    assert not asyncio.run(provider.verify_checksum(record.file_id, context))
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.read(record.file_id, context.operation))
    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION

    (root / record.file_id).unlink()
    orphan_report = asyncio.run(provider.detect_orphans(context))
    assert orphan_report.missing_objects == (record.file_id,)


def test_file_project_scope_isolation(tmp_path: Path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    provider = LocalFileProvider(tmp_path / "objects", tmp_path / "data.sqlite")
    record = asyncio.run(provider.create_file(b"a", _context(project_a)))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_file(record.file_id, _context(project_b)))
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def _entry(scope: MemoryScope, scope_id: str, *, historical: bool = False) -> MemoryEntry:
    now = datetime.now(UTC)
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"fact": scope.value},
        created_at=now,
        retention={
            MemoryScope.TASK: RetentionPolicy.TASK_LIFETIME,
            MemoryScope.AGENT: RetentionPolicy.DURABLE,
            MemoryScope.WORKSPACE: RetentionPolicy.PROJECT_LIFETIME,
            MemoryScope.USER: RetentionPolicy.USER_LIFETIME,
            MemoryScope.HISTORICAL: RetentionPolicy.DURABLE,
            MemoryScope.SHORT_TERM: RetentionPolicy.EPHEMERAL,
        }[scope],
        expires_at=now + timedelta(minutes=10) if scope is MemoryScope.SHORT_TERM else None,
        provenance=(SourceRef(kind="event", ref="event-source"),) if historical else (),
    )


def test_memory_write_read_query_for_all_six_scopes(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")
    cases = (
        (MemoryScope.SHORT_TERM, "session-1", False),
        (MemoryScope.TASK, new_id("task"), False),
        (MemoryScope.AGENT, new_id("agent"), False),
        (MemoryScope.WORKSPACE, project_id, False),
        (MemoryScope.USER, "user-a", False),
        (MemoryScope.HISTORICAL, "history:project", True),
    )

    for scope, scope_id, historical in cases:
        entry = _entry(scope, scope_id, historical=historical)
        written = asyncio.run(provider.write_entry(entry, context))
        assert asyncio.run(provider.get_entry(written.memory_id, context)) == written
        queried = asyncio.run(provider.query_entries(MemoryQuery(scope, scope_id), context))
        assert queried == (written,)


def test_memory_scope_isolation_and_user_boundary(tmp_path: Path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")
    entry = _entry(MemoryScope.WORKSPACE, project_a)
    asyncio.run(provider.write_entry(entry, _context(project_a)))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(entry.memory_id, _context(project_b)))
    assert exc_info.value.code is ErrorCode.FORBIDDEN

    user_entry = _entry(MemoryScope.USER, "user-a")
    asyncio.run(provider.write_entry(user_entry, _context(project_a, "user-a")))
    with pytest.raises(ContractError) as user_exc:
        asyncio.run(provider.get_entry(user_entry.memory_id, _context(project_a, "user-b")))
    assert user_exc.value.code is ErrorCode.FORBIDDEN


def test_memory_expiry_supersession_and_search(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")
    context = _context()
    task_id = new_id("task")
    current = _entry(MemoryScope.TASK, task_id)
    asyncio.run(provider.write_entry(current, context))

    replacement = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.TASK,
        scope_id=task_id,
        owner_ref=current.owner_ref,
        created_by=current.created_by,
        value={"fact": "replacement"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.TASK_LIFETIME,
        provenance=(SourceRef(kind="memory", ref=current.memory_id),),
    )
    superseded = asyncio.run(provider.supersede_entry(current.memory_id, replacement, context))
    assert superseded.supersedes_memory_id == current.memory_id
    visible = asyncio.run(provider.query_entries(MemoryQuery(MemoryScope.TASK, task_id), context))
    assert visible == (superseded,)
    assert asyncio.run(
        provider.search_entries(MemoryQuery(MemoryScope.TASK, task_id), "replacement", context)
    ) == (superseded,)

    now = datetime.now(UTC)
    expired = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.SHORT_TERM,
        scope_id="session-expired",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="temporary",
        created_at=now - timedelta(hours=2),
        retention=RetentionPolicy.EPHEMERAL,
        expires_at=now - timedelta(hours=1),
    )
    asyncio.run(provider.write_entry(expired, context))
    expired_ids = asyncio.run(provider.expire_entries(context))
    assert expired.memory_id in expired_ids
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(expired.memory_id, context))
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_historical_memory_requires_provenance() -> None:
    with pytest.raises(ValueError, match="historical memory requires provenance"):
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.HISTORICAL,
            scope_id="history",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="summary",
            created_at=datetime.now(UTC),
            retention=RetentionPolicy.DURABLE,
        )


def test_memory_persists_across_provider_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite"
    task_id = new_id("task")
    context = _context()
    first = LocalMemoryProvider(db_path)
    entry = _entry(MemoryScope.TASK, task_id)
    asyncio.run(first.write_entry(entry, context))

    second = LocalMemoryProvider(db_path)
    assert asyncio.run(second.get_entry(entry.memory_id, context)) == entry


def test_knowledge_register_index_search_reindex_remove(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalKnowledgeProvider(tmp_path / "data.sqlite")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Architecture notes",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    asyncio.run(provider.register_source(source, context))
    document = asyncio.run(
        provider.ingest_source(
            source.source_id,
            "canonical task state remains authoritative",
            "line:1",
            context,
        )
    )
    assert document.source_id == source.source_id
    index = asyncio.run(provider.get_index_status(source.source_id, context))
    assert index.status is KnowledgeStatus.READY

    results = asyncio.run(
        provider.search(
            KnowledgeSearchRequest(
                query="canonical authoritative",
                context=context,
                source_ids=(source.source_id,),
            )
        )
    )
    assert len(results) == 1
    assert results[0].citation.ref == document.document_id
    assert results[0].citation.location == "line:1"

    reindexed = asyncio.run(
        provider.reindex_source(
            source.source_id,
            "r2",
            "updated source revision",
            "line:2",
            context,
        )
    )
    assert reindexed.revision == "r2"
    assert asyncio.run(provider.get_index_status(source.source_id, context)).revision == "r2"

    asyncio.run(provider.remove_source(source.source_id, context))
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_index_status(source.source_id, context))
    assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_knowledge_does_not_require_semantic_backend(tmp_path: Path) -> None:
    provider = LocalKnowledgeProvider(tmp_path / "data.sqlite")
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            provider.search(
                KnowledgeSearchRequest(
                    query="test",
                    context=_context(),
                    mode=KnowledgeSearchMode.SEMANTIC,
                )
            )
        )
    assert exc_info.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_knowledge_project_isolation(tmp_path: Path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    provider = LocalKnowledgeProvider(tmp_path / "data.sqlite")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_a,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="private project source",
        revision="1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    asyncio.run(provider.register_source(source, _context(project_a)))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.ingest_source(source.source_id, "data", "source", _context(project_b)))
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_control_plane_provider_bundle_is_backend_neutral(tmp_path: Path) -> None:
    bundle = DataProviderSet(
        files=LocalFileProvider(tmp_path / "objects", tmp_path / "data.sqlite"),
        memory=LocalMemoryProvider(tmp_path / "data.sqlite"),
        knowledge=LocalKnowledgeProvider(tmp_path / "data.sqlite"),
    )
    assert bundle.files.descriptor.provider_type == "file"
    assert bundle.memory.descriptor.provider_type == "memory"
    assert bundle.knowledge.descriptor.provider_type == "knowledge"
