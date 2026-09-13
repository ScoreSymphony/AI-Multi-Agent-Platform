from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.service import ScopeStore
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
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


class ToggleMemoryProvider(LocalMemoryProvider):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.discovery_available = True

    async def list_entries_for_discovery(self) -> tuple[MemoryEntry, ...]:
        if not self.discovery_available:
            raise ContractError(ErrorCode.UNAVAILABLE, "memory discovery unavailable")
        return await super().list_entries_for_discovery()


class ToggleKnowledgeProvider(LocalKnowledgeProvider):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.discovery_available = True

    async def list_sources_for_discovery(self) -> tuple[KnowledgeSource, ...]:
        if not self.discovery_available:
            raise ContractError(ErrorCode.TRANSIENT_FAILURE, "knowledge discovery unavailable")
        return await super().list_sources_for_discovery()


def _access(project_id: str | None = None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-290-degraded",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def _stack(providers: DataProviderSet, scopes: ScopeStore) -> ControlPlaneHTTP:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        scopes=scopes,
        resource_services=data_resource_services(
            providers,
            project_ids=lambda: tuple(project.id for project in scopes.list_projects()),
        ),
    )
    return ControlPlaneHTTP(control_plane)


async def _search(http: ControlPlaneHTTP, resource_type: str, resource_id: str) -> int:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers={
                "X-Principal-Ref": "user:alice",
                "X-Owner-Type": "user",
                "X-Owner-Id": "alice",
            },
            query={"type": resource_type, "id": resource_id},
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    total = response.body["total"]
    assert isinstance(total, int)
    return total


def test_degraded_discovery_rebuild_removes_previously_indexed_memory_and_knowledge(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        memory = ToggleMemoryProvider(tmp_path / "memory.sqlite3")
        knowledge = ToggleKnowledgeProvider(tmp_path / "knowledge.sqlite3")
        providers = DataProviderSet(
            files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
            memory=memory,
            knowledge=knowledge,
        )
        scopes = ScopeStore()
        project = scopes.create_project(
            key="issue-290-degraded",
            name="Issue 290 degraded rebuild",
            owner_type="user",
            owner_id="alice",
        )

        memory_entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            created_by="user:alice",
            value={"private": "stale-memory-content"},
            created_at=datetime.now(UTC),
            retention=RetentionPolicy.DURABLE,
        )
        await memory.write_entry(memory_entry, _access())

        now = datetime.now(UTC)
        source = KnowledgeSource(
            source_id=new_knowledge_source_id(),
            project_id=project.id,
            owner_ref="user:alice",
            created_by="user:alice",
            title="Stale knowledge source",
            revision="1",
            status=KnowledgeStatus.REGISTERED,
            created_at=now,
            updated_at=now,
        )
        await knowledge.register_source(source, _access(project.id))
        document = await knowledge.ingest_source(
            source.source_id,
            "stale knowledge content",
            "private://backend/location",
            _access(project.id),
        )

        http = _stack(providers, scopes)
        assert await _search(http, "memory", memory_entry.memory_id) == 1
        assert await _search(http, "knowledge-source", source.source_id) == 1
        assert await _search(http, "knowledge-document", document.document_id) == 1

        memory.discovery_available = False
        knowledge.discovery_available = False

        assert await _search(http, "memory", memory_entry.memory_id) == 0
        assert await _search(http, "knowledge-source", source.source_id) == 0
        assert await _search(http, "knowledge-document", document.document_id) == 0

    asyncio.run(scenario())
