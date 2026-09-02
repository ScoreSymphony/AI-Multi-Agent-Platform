from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import (
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
    StoredObject,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryProvider,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_file_id,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


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


def test_every_durable_memory_scope_has_provenance_semantics() -> None:
    project_id = new_id("project")
    cases = (
        (MemoryScope.TASK, new_id("task")),
        (MemoryScope.AGENT, new_id("agent")),
        (MemoryScope.WORKSPACE, project_id),
        (MemoryScope.USER, "user-a"),
    )

    for scope, scope_id in cases:
        entry = _durable_entry(scope, scope_id)
        assert entry.provenance == (SourceRef(kind="memory_writer", ref="user:user-a"),)

    historical = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.HISTORICAL,
        scope_id="history:task",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="summary",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        provenance=(SourceRef(kind="event", ref="event-canonical-evidence"),),
    )
    assert historical.provenance[0].kind == "event"


def test_memory_access_semantics_are_explicit_for_all_six_scopes() -> None:
    project_id = new_id("project")
    now = datetime.now(UTC)
    entries = (
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.SHORT_TERM,
            scope_id="execution-1",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="context",
            created_at=now,
            retention=RetentionPolicy.EPHEMERAL,
            expires_at=now + timedelta(minutes=15),
        ),
        _durable_entry(MemoryScope.TASK, new_id("task")),
        _durable_entry(MemoryScope.AGENT, new_id("agent")),
        _durable_entry(MemoryScope.WORKSPACE, project_id),
        _durable_entry(MemoryScope.USER, "user-a"),
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.HISTORICAL,
            scope_id="history:project",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="history",
            created_at=now,
            retention=RetentionPolicy.DURABLE,
            provenance=(SourceRef(kind="event", ref="event-1"),),
        ),
    )

    for entry in entries:
        policy = entry.access_policy
        assert policy.readers
        assert policy.writers
        assert policy.agent_revision_access
        assert policy.team_access
        assert policy.task_inheritance
        assert policy.cross_project_access

    assert entries[0].execution_ref == "execution-1"
    assert entries[0].access_policy.agent_revision_access == "context_bound"
    assert entries[2].access_policy.agent_revision_access == "same_agent_policy_controlled"
    assert entries[2].access_policy.team_access == "explicit_policy_only"
    assert entries[3].access_policy.task_inheritance == "explicit_only"
    assert entries[4].access_policy.cross_project_access == "explicit_policy_only"


class InMemoryReplacementMemoryProvider(MemoryProvider):
    """Second backend used to prove the canonical contract is replaceable."""

    def __init__(self) -> None:
        self.entries: dict[str, MemoryEntry] = {}
        self._descriptor = ProviderDescriptor(
            provider_id="in-memory-contract-test",
            provider_type="memory",
            supported_operations=("write", "get"),
            capabilities=(),
            health=HealthStatus.HEALTHY,
        )

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._descriptor

    async def put(
        self,
        namespace: str,
        key: str,
        value: JsonValue,
        context: OperationContext,
        *,
        metadata: dict[str, JsonValue] | None = None,
    ) -> StoredObject:
        raise NotImplementedError

    async def get(self, namespace: str, key: str, context: OperationContext) -> JsonValue:
        raise NotImplementedError

    async def write_entry(self, entry: MemoryEntry, context: DataAccessContext) -> MemoryEntry:
        self.entries[entry.memory_id] = entry
        return entry

    async def get_entry(self, memory_id: str, context: DataAccessContext) -> MemoryEntry:
        try:
            return self.entries[memory_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"memory not found: {memory_id}") from exc

    async def query_entries(
        self,
        query: MemoryQuery,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        return tuple(
            entry
            for entry in self.entries.values()
            if entry.scope is query.scope and entry.scope_id == query.scope_id
        )[: query.limit]

    async def search_entries(
        self,
        query: MemoryQuery,
        text: str,
        context: DataAccessContext,
    ) -> tuple[MemoryEntry, ...]:
        return await self.query_entries(query, context)

    async def supersede_entry(
        self,
        memory_id: str,
        replacement: MemoryEntry,
        context: DataAccessContext,
    ) -> MemoryEntry:
        self.entries[replacement.memory_id] = replacement
        return replacement

    async def delete_entry(self, memory_id: str, context: DataAccessContext) -> None:
        self.entries.pop(memory_id, None)

    async def expire_entries(self, context: DataAccessContext) -> tuple[str, ...]:
        return ()


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


def test_local_provider_maps_backend_failure_to_canonical_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")

    def fail_connect() -> sqlite3.Connection:
        raise sqlite3.OperationalError("simulated backend outage")

    monkeypatch.setattr(provider, "_connect", fail_connect)
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(new_memory_id(), _context()))
    assert exc_info.value.code is ErrorCode.BACKEND_ERROR


def test_missing_file_and_knowledge_references_map_to_not_found(tmp_path: Path) -> None:
    context = _context(new_id("project"))
    files = LocalFileProvider(tmp_path / "files", tmp_path / "data.sqlite")
    knowledge = LocalKnowledgeProvider(tmp_path / "data.sqlite")

    with pytest.raises(ContractError) as file_exc:
        asyncio.run(files.get_file(new_file_id(), context))
    assert file_exc.value.code is ErrorCode.NOT_FOUND

    with pytest.raises(ContractError) as knowledge_exc:
        asyncio.run(knowledge.get_index_status(new_knowledge_source_id(), context))
    assert knowledge_exc.value.code is ErrorCode.NOT_FOUND


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
