from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import ai_multi_agent_platform.data.reference_lifecycle as reference_lifecycle
from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.control_plane import ControlPlane, ControlPlaneHTTP, HTTPRequest
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _stack(
    providers: DataProviderSet,
    scopes: ScopeStore,
) -> tuple[ControlPlane, ControlPlaneHTTP]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )

    def project_ids() -> tuple[str, ...]:
        return tuple(project.id for project in scopes.list_projects())

    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        scopes=scopes,
        resource_services=data_resource_services(providers, project_ids=project_ids),
        command_handlers=data_command_handlers(providers, project_ids=project_ids),
    )
    return control_plane, ControlPlaneHTTP(control_plane)


def _headers(*, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Principal-Ref": "user:alice",
        "X-Owner-Type": "user",
        "X-Owner-Id": "alice",
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


async def _search(http: ControlPlaneHTTP, **query: str) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="GET",
            path="/api/v1/search",
            headers=_headers(),
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


async def _command(
    http: ControlPlaneHTTP,
    command: str,
    resource_ref: str,
    payload: dict[str, object],
    *,
    idempotency_key: str,
) -> dict[str, object]:
    response = await http.handle(
        HTTPRequest(
            method="POST",
            path=f"/api/v1/commands/{command}",
            headers=_headers(idempotency_key=idempotency_key),
            body={"resource_ref": resource_ref, **payload},
        )
    )
    assert response.status == 200, response.body
    assert isinstance(response.body, dict)
    return response.body


def test_live_memory_disappears_from_global_search_after_expiry_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FrozenDateTime(datetime):
        current = datetime(2026, 9, 6, 0, 0, tzinfo=UTC)

        @classmethod
        def now(cls, tz=None):
            current = cls.current
            if tz is None:
                return current.replace(tzinfo=None)
            return current.astimezone(tz)

    async def scenario() -> None:
        monkeypatch.setattr(reference_lifecycle, "datetime", FrozenDateTime)
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        _control_plane, http = _stack(providers, scopes)

        entry = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            created_by="user:alice",
            value={"private": "expiry-transition-private"},
            created_at=FrozenDateTime.current,
            retention=RetentionPolicy.USER_LIFETIME,
            expires_at=FrozenDateTime.current + timedelta(minutes=1),
            origin=MemoryOrigin.USER_AUTHORED,
        )
        await providers.memory.write_entry(
            entry,
            DataAccessContext(
                operation=OperationContext(
                    correlation_id="issue-290-expiry-transition",
                    owner_type="user",
                    owner_id="alice",
                ),
                actor_ref="user:alice",
            ),
        )

        before = await _search(http, type="memory", id=entry.memory_id)
        assert before["total"] == 1

        FrozenDateTime.current = FrozenDateTime.current + timedelta(minutes=2)

        after = await _search(http, type="memory", id=entry.memory_id)
        assert after["total"] == 0
        assert entry.memory_id not in repr(after)

    asyncio.run(scenario())


def test_northbound_memory_and_knowledge_updates_propagate_into_global_search(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        providers = _providers(tmp_path)
        scopes = ScopeStore()
        project = scopes.create_project(
            key="issue-290-hardening",
            name="Issue 290 hardening",
            owner_type="user",
            owner_id="alice",
        )
        _control_plane, http = _stack(providers, scopes)

        created_memory = await _command(
            http,
            "memory.create",
            "alice",
            {
                "scope": "user",
                "origin": "user-authored",
                "value": {"private": "memory-update-private"},
                "retention": "durable",
            },
            idempotency_key="issue-290-memory-create",
        )
        memory_id = created_memory["id"]
        assert isinstance(memory_id, str)
        assert (await _search(http, type="memory", id=memory_id))["total"] == 1

        updated_memory = await _command(
            http,
            "memory.update",
            memory_id,
            {"retention": "user_lifetime"},
            idempotency_key="issue-290-memory-update",
        )
        updated_memory_id = updated_memory["id"]
        assert isinstance(updated_memory_id, str)
        assert updated_memory_id != memory_id
        assert (await _search(http, type="memory", id=memory_id))["total"] == 0
        assert (await _search(http, type="memory", id=updated_memory_id))["total"] == 1
        by_retention = await _search(http, type="memory", q="user_lifetime")
        assert {item["resource_id"] for item in _items(by_retention)} == {updated_memory_id}

        registered_source = await _command(
            http,
            "knowledge.register",
            project.id,
            {
                "project_id": project.id,
                "title": "prechangeglyph",
                "revision": "r1",
            },
            idempotency_key="issue-290-knowledge-register",
        )
        source_id = registered_source["id"]
        assert isinstance(source_id, str)

        ingested_document = await _command(
            http,
            "knowledge.ingest",
            source_id,
            {
                "content": "knowledge-update-private-content",
                "location": "private://knowledge/update-source",
            },
            idempotency_key="issue-290-knowledge-ingest",
        )
        document_id = ingested_document["id"]
        assert isinstance(document_id, str)

        source_before = await _search(http, type="knowledge-source", q="prechangeglyph")
        document_before = await _search(http, type="knowledge-document", q="prechangeglyph")
        assert source_before["total"] == 1
        assert document_before["total"] == 1

        updated_source = await _command(
            http,
            "knowledge.update",
            source_id,
            {"title": "postchangequartz"},
            idempotency_key="issue-290-knowledge-update",
        )
        assert updated_source["id"] == source_id
        assert updated_source["title"] == "postchangequartz"

        source_old_title = await _search(http, type="knowledge-source", q="prechangeglyph")
        document_old_title = await _search(http, type="knowledge-document", q="prechangeglyph")
        source_new_title = await _search(http, type="knowledge-source", q="postchangequartz")
        updated_document = await _search(http, type="knowledge-document", q="postchangequartz")

        assert source_old_title["total"] == 0
        assert document_old_title["total"] == 0
        assert source_new_title["total"] == 1
        assert updated_document["total"] == 1
        assert _items(updated_document)[0]["resource_id"] == document_id

    asyncio.run(scenario())
