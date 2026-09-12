from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryScope,
    RetentionPolicy,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.testing import FakeScopedMemoryProvider


def _operation(project_id: str | None = None, owner_id: str = "user-a") -> OperationContext:
    return OperationContext(
        correlation_id="corr-hardening",
        owner_type="user",
        owner_id=owner_id,
        project_id=project_id,
    )


def _context(project_id: str | None = None, owner_id: str = "user-a") -> DataAccessContext:
    return DataAccessContext(
        operation=_operation(project_id, owner_id),
        actor_ref=f"user:{owner_id}",
    )


def _durable_entry(scope: MemoryScope, scope_id: str) -> MemoryEntry:
    retention = {
        MemoryScope.TASK: RetentionPolicy.TASK_LIFETIME,
        MemoryScope.AGENT: RetentionPolicy.DURABLE,
        MemoryScope.WORKSPACE: RetentionPolicy.PROJECT_LIFETIME,
        MemoryScope.USER: RetentionPolicy.USER_LIFETIME,
    }[scope]
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"scope": scope.value},
        created_at=datetime.now(UTC),
        retention=retention,
    )


class InMemoryReplacementMemoryProvider(FakeScopedMemoryProvider):
    """Compatibility alias for the reusable refined replacement backend."""


def test_backend_replacement_preserves_canonical_memory_identity(tmp_path: Path) -> None:
    context = _context()
    task_id = new_id("task")
    canonical = _durable_entry(MemoryScope.TASK, task_id)

    local = LocalMemoryProvider(tmp_path / "local.sqlite")
    stored = asyncio.run(local.write_entry(canonical, context))
    reloaded = asyncio.run(local.get_entry(stored.memory_id, context))

    replacement = InMemoryReplacementMemoryProvider()
    migrated = asyncio.run(replacement.write_entry(reloaded, context))

    bundle = DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "data.sqlite"),
        memory=replacement,
        knowledge=LocalKnowledgeProvider(tmp_path / "data.sqlite"),
    )
    assert bundle.memory is replacement
    assert migrated.memory_id == canonical.memory_id
    assert (
        asyncio.run(bundle.memory.get_entry(canonical.memory_id, context)).memory_id
        == canonical.memory_id
    )


def test_canonical_knowledge_source_id_survives_lifecycle(tmp_path: Path) -> None:
    project_id = new_id("project")
    context = _context(project_id)
    provider = LocalKnowledgeProvider(tmp_path / "data.sqlite")
    source_id = new_knowledge_source_id()
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=source_id,
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="ID stability",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )

    registered = asyncio.run(provider.register_source(source, context))
    document = asyncio.run(provider.ingest_source(source_id, "alpha", "line:1", context))
    reindexed = asyncio.run(provider.reindex_source(source_id, "r2", "beta", "line:2", context))

    assert registered.source_id == source_id
    assert document.source_id == source_id
    assert reindexed.source_id == source_id
    assert asyncio.run(provider.get_index_status(source_id, context)).source_id == source_id
