from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AuthorizationDecision,
    AuthorizationRequest,
    OperationContext,
)
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
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class DataSearchAuthorization(FakeAuthorizationProvider):
    def __init__(
        self,
        *,
        denied_owner_ids: frozenset[str] = frozenset(),
        denied_project_ids: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self.denied_owner_ids = denied_owner_ids
        self.denied_project_ids = denied_project_ids

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.context.owner_id in self.denied_owner_ids:
            return AuthorizationDecision(allowed=False, reason="owner-hidden")
        if request.context.project_id in self.denied_project_ids:
            return AuthorizationDecision(allowed=False, reason="project-hidden")
        return AuthorizationDecision(allowed=True, reason="data-search-visible")


def _headers(owner_id: str = "alice") -> dict[str, str]:
    return {
        "X-Principal-Ref": f"user:{owner_id}",
        "X-Owner-Type": "user",
        "X-Owner-Id": owner_id,
    }


def _access(
    *,
    owner_type: str = "user",
    owner_id: str = "alice",
    project_id: str | None = None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-290-data",
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref=f"{owner_type}:{owner_id}",
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _stack(
    providers: DataProviderSet,
    scopes: ScopeStore,
    authorization: FakeAuthorizationProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
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
        authorization=authorization,
        resource_services=data_resource_services(
            providers,
            project_ids=lambda: tuple(project.id for project in scopes.list_projects()),
        ),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


async def _search(
    http: ControlPlaneHTTP,
    *,
    owner_id: str = "alice",
    **query: str,
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(owner_id),
            query=query,
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def _items(page: dict[str, object]) -> list[dict[str, object]]:
    items = page["items"]
    assert isinstance(items, list)
    assert all(isinstance(item, dict) for item in items)
    return items


async def _memory(
    provider: LocalMemoryProvider,
    *,
    scope: MemoryScope,
    scope_id: str,
    owner_ref: str,
    project_id: str | None = None,
    value: str,
    origin: MemoryOrigin = MemoryOrigin.USER_AUTHORED,
) -> MemoryEntry:
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref=owner_ref,
        created_by="user:alice",
        value={"text": value},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        origin=origin,
        classification="internal",
        metadata={
            "provider_index_id": "provider-private-memory-index",
            "embedding": [0.1, 0.2, 0.3],
            "private_note": "memory-private-metadata-needle",
        },
    )
    return await provider.write_entry(
        entry,
        _access(
            owner_type="organization" if scope is MemoryScope.ORGANIZATION else "user",
            owner_id=scope_id if scope is MemoryScope.ORGANIZATION else "alice",
            project_id=project_id,
        ),
    )


async def _knowledge(
    provider: LocalKnowledgeProvider,
    *,
    project_id: str,
    owner_ref: str,
    title: str,
) -> tuple[KnowledgeSource, str]:
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref=owner_ref,
        created_by=owner_ref,
        title=title,
        revision="1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
        metadata={
            "provider_index_id": "provider-private-knowledge-index",
            "vector_collection": "provider-private-vector-collection",
        },
    )
    await provider.register_source(source, _access(owner_id=owner_ref.split(":", 1)[1], project_id=project_id))
    document = await provider.ingest_source(
        source.source_id,
        "knowledge-private-content-needle that must stay outside global Search",
        "/srv/provider/private/backend/path.txt",
        _access(owner_id=owner_ref.split(":", 1)[1], project_id=project_id),
    )
    return source, document.document_id


def test_memory_and_knowledge_are_discoverable_by_canonical_identity_without_private_content(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        project = scopes.create_project(
            key="issue-290-visible",
            name="Issue 290 visible",
            owner_type="user",
            owner_id="alice",
        )
        memory = await _memory(
            providers.memory,
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            value="memory-private-content-needle",
        )
        source, document_id = await _knowledge(
            providers.knowledge,
            project_id=project.id,
            owner_ref="user:alice",
            title="Canonical research notes",
        )
        control_plane, http = _stack(providers, scopes)

        exact_memory = await _search(http, type="memory", id=memory.memory_id)
        assert exact_memory["total"] == 1
        memory_result = _items(exact_memory)[0]
        assert memory_result["resource_id"] == memory.memory_id
        assert memory_result["owner_type"] == "user"
        assert memory_result["owner_id"] == "alice"
        assert memory_result["canonical_ref"] == f"/api/v1/memory/{memory.memory_id}"

        exact_source = await _search(http, type="knowledge-source", id=source.source_id)
        assert exact_source["total"] == 1
        source_result = _items(exact_source)[0]
        assert source_result["resource_id"] == source.source_id
        assert source_result["project_id"] == project.id
        assert source_result["canonical_ref"] == f"/api/v1/knowledge/{source.source_id}"

        exact_document = await _search(http, type="knowledge-document", id=document_id)
        assert exact_document["total"] == 1
        document_result = _items(exact_document)[0]
        assert document_result["resource_id"] == document_id
        assert document_result["project_id"] == project.id
        assert document_result["canonical_ref"] == f"/api/v1/knowledge-documents/{document_id}"

        by_title = await _search(http, type="knowledge-document", q="Canonical research notes")
        assert {item["resource_id"] for item in _items(by_title)} == {document_id}

        for private_needle in (
            "memory-private-content-needle",
            "memory-private-metadata-needle",
            "provider-private-memory-index",
            "provider-private-knowledge-index",
            "provider-private-vector-collection",
            "knowledge-private-content-needle",
            "/srv/provider/private/backend/path.txt",
        ):
            page = await _search(http, q=private_needle)
            assert page["total"] == 0, private_needle
            assert private_needle not in json.dumps(page, sort_keys=True)

        rebuilt = await control_plane.rebuild_search_index()
        assert rebuilt >= 3
        assert (await _search(http, type="memory", id=memory.memory_id))["total"] == 1
        assert (await _search(http, type="knowledge-document", id=document_id))["total"] == 1

    asyncio.run(scenario())


def test_user_agent_project_and_organization_scope_authorization_filters_before_counts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        visible_project = scopes.create_project(
            key="issue-290-visible-project",
            name="Visible project",
            owner_type="user",
            owner_id="alice",
        )
        hidden_project = scopes.create_project(
            key="issue-290-hidden-project",
            name="Hidden project",
            owner_type="user",
            owner_id="bob",
        )
        visible_user = await _memory(
            providers.memory,
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            value="visible-user-memory",
        )
        hidden_user = await _memory(
            providers.memory,
            scope=MemoryScope.USER,
            scope_id="bob",
            owner_ref="user:bob",
            value="hidden-user-memory",
        )
        hidden_agent = await _memory(
            providers.memory,
            scope=MemoryScope.AGENT,
            scope_id="agent_hidden",
            owner_ref="user:alice",
            value="hidden-agent-memory",
            origin=MemoryOrigin.AGENT_DERIVED,
        )
        visible_workspace = await _memory(
            providers.memory,
            scope=MemoryScope.WORKSPACE,
            scope_id=visible_project.id,
            owner_ref="user:alice",
            project_id=visible_project.id,
            value="visible-workspace-memory",
        )
        hidden_workspace = await _memory(
            providers.memory,
            scope=MemoryScope.WORKSPACE,
            scope_id=hidden_project.id,
            owner_ref="user:bob",
            project_id=hidden_project.id,
            value="hidden-workspace-memory",
        )
        hidden_org = await _memory(
            providers.memory,
            scope=MemoryScope.ORGANIZATION,
            scope_id="org_hidden",
            owner_ref="organization:org_hidden",
            value="hidden-organization-memory",
        )
        hidden_source, hidden_document = await _knowledge(
            providers.knowledge,
            project_id=hidden_project.id,
            owner_ref="user:bob",
            title="Hidden knowledge",
        )

        authorization = DataSearchAuthorization(
            denied_owner_ids=frozenset({"bob", "agent_hidden", "org_hidden"}),
            denied_project_ids=frozenset({hidden_project.id}),
        )
        _control_plane, http = _stack(providers, scopes, authorization)

        visible = await _search(http, type="memory")
        visible_ids = {item["resource_id"] for item in _items(visible)}
        assert visible_user.memory_id in visible_ids
        assert visible_workspace.memory_id in visible_ids
        assert hidden_user.memory_id not in visible_ids
        assert hidden_agent.memory_id not in visible_ids
        assert hidden_workspace.memory_id not in visible_ids
        assert hidden_org.memory_id not in visible_ids

        for hidden_id, resource_type in (
            (hidden_user.memory_id, "memory"),
            (hidden_agent.memory_id, "memory"),
            (hidden_workspace.memory_id, "memory"),
            (hidden_org.memory_id, "memory"),
            (hidden_source.source_id, "knowledge-source"),
            (hidden_document, "knowledge-document"),
        ):
            page = await _search(http, type=resource_type, id=hidden_id)
            assert page["total"] == 0
            assert hidden_id not in json.dumps(page, sort_keys=True)

        assert any(
            call.action == "memory:list"
            and call.context.owner_type == "agent"
            and call.context.owner_id == "agent_hidden"
            for call in authorization.calls
        )
        assert any(
            call.action == "memory:list"
            and call.context.owner_type == "organization"
            and call.context.owner_id == "org_hidden"
            for call in authorization.calls
        )
        assert any(
            call.action == "knowledge-document:list"
            and call.context.project_id == hidden_project.id
            for call in authorization.calls
        )

    asyncio.run(scenario())


def test_memory_updates_and_knowledge_detach_or_reindex_replace_search_documents(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        project = scopes.create_project(
            key="issue-290-lifecycle",
            name="Lifecycle project",
            owner_type="user",
            owner_id="alice",
        )
        original = await _memory(
            providers.memory,
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            value="old memory",
        )
        source, document_id = await _knowledge(
            providers.knowledge,
            project_id=project.id,
            owner_ref="user:alice",
            title="Lifecycle knowledge",
        )
        _control_plane, http = _stack(providers, scopes)

        assert (await _search(http, type="memory", id=original.memory_id))["total"] == 1
        replacement = MemoryEntry(
            memory_id=new_memory_id(),
            scope=original.scope,
            scope_id=original.scope_id,
            owner_ref=original.owner_ref,
            created_by="user:alice",
            value={"text": "replacement"},
            created_at=datetime.now(UTC),
            retention=original.retention,
            origin=original.origin,
            supersedes_memory_id=original.memory_id,
        )
        replacement = await providers.memory.supersede_entry(
            original.memory_id,
            replacement,
            _access(),
        )
        assert (await _search(http, type="memory", id=original.memory_id))["total"] == 0
        assert (await _search(http, type="memory", id=replacement.memory_id))["total"] == 1
        await providers.memory.delete_entry(replacement.memory_id, _access())
        assert (await _search(http, type="memory", id=replacement.memory_id))["total"] == 0

        assert (await _search(http, type="knowledge-document", id=document_id))["total"] == 1
        new_document = await providers.knowledge.reindex_source(
            source.source_id,
            "2",
            "replacement knowledge payload",
            "portable://knowledge/revision-2",
            _access(project_id=project.id),
        )
        assert (await _search(http, type="knowledge-document", id=document_id))["total"] == 0
        assert (
            await _search(http, type="knowledge-document", id=new_document.document_id)
        )["total"] == 1

        await providers.knowledge.remove_source(source.source_id, _access(project_id=project.id))
        assert (await _search(http, type="knowledge-source", id=source.source_id))["total"] == 0
        assert (
            await _search(http, type="knowledge-document", id=new_document.document_id)
        )["total"] == 0

    asyncio.run(scenario())


def test_discovery_provider_unavailability_fails_closed_without_stale_memory_or_knowledge(
    tmp_path: Path,
) -> None:
    class UnavailableMemory(LocalMemoryProvider):
        async def list_entries_for_discovery(self) -> tuple[MemoryEntry, ...]:
            raise ContractError(ErrorCode.UNAVAILABLE, "memory discovery unavailable")

    class UnavailableKnowledge(LocalKnowledgeProvider):
        async def list_sources_for_discovery(self) -> tuple[KnowledgeSource, ...]:
            raise ContractError(ErrorCode.TRANSIENT_FAILURE, "knowledge discovery unavailable")

    async def scenario() -> None:
        providers = DataProviderSet(
            files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
            memory=UnavailableMemory(tmp_path / "memory.sqlite3"),
            knowledge=UnavailableKnowledge(tmp_path / "knowledge.sqlite3"),
        )
        scopes = ScopeStore()
        _control_plane, http = _stack(providers, scopes)
        assert (await _search(http, type="memory"))["total"] == 0
        assert (await _search(http, type="knowledge-source"))["total"] == 0
        assert (await _search(http, type="knowledge-document"))["total"] == 0

    asyncio.run(scenario())
