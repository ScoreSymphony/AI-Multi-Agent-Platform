from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
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
from ai_multi_agent_platform.domain import new_id


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-251",
        correlation_id="correlation-251",
        actor=ActorContext(
            principal_ref="user:user-a",
            owner_type="user",
            owner_id="user-a",
        ),
    )


def _data_context(project_id: str | None = None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="correlation-251",
            owner_type="user",
            owner_id="user-a",
            project_id=project_id,
        ),
        actor_ref="user:user-a",
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def test_memory_resource_defaults_to_authenticated_users_own_scope(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"preference": "canonical"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        origin=MemoryOrigin.USER_AUTHORED,
    )
    asyncio.run(providers.memory.write_entry(entry, _data_context()))

    service = data_resource_services(providers)["memory"]
    resources = asyncio.run(service.list_resources(_request_context(), PageQuery()))

    assert len(resources) == 1
    assert resources[0]["id"] == entry.memory_id
    assert resources[0]["scope"] == "user"
    assert resources[0]["scope_id"] == "user-a"
    assert resources[0]["origin"] == "user-authored"
    assert resources[0]["value"] == {"preference": "canonical"}


def test_memory_resource_requires_explicit_non_user_scope_binding(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    context = RequestContext(
        request_id="request-service",
        correlation_id="correlation-service",
        actor=ActorContext(
            principal_ref="service:worker",
            owner_type="service",
            owner_id="worker",
        ),
    )
    service = data_resource_services(providers)["memory"]

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(service.list_resources(context, PageQuery()))
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_knowledge_sources_and_query_results_are_distinct_resources(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    project_id = new_id("project")
    context = _data_context(project_id)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        title="Canonical architecture",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    asyncio.run(providers.knowledge.register_source(source, context))
    document = asyncio.run(
        providers.knowledge.ingest_source(
            source.source_id,
            "canonical memory and knowledge stay provider neutral",
            "section:memory",
            context,
        )
    )

    services = data_resource_services(providers, project_ids=lambda: (project_id,))
    source_resources = asyncio.run(
        services["knowledge"].list_resources(
            _request_context(),
            PageQuery(filters={"project_id": project_id}),
        )
    )
    assert len(source_resources) == 1
    assert source_resources[0]["id"] == source.source_id
    assert source_resources[0]["type"] == "knowledge-source"
    assert "index_id" not in source_resources[0]

    results = asyncio.run(
        services["knowledge-results"].list_resources(
            _request_context(),
            PageQuery(
                search="canonical provider neutral",
                filters={"project_id": project_id},
            ),
        )
    )
    assert len(results) == 1
    result = results[0]
    assert result["id"] == document.document_id
    assert result["source_id"] == source.source_id
    assert result["type"] == "knowledge-result"
    assert result["citation"]["ref"] == document.document_id
    assert result["citation"]["location"] == "section:memory"
    assert "index_id" not in result


def test_knowledge_results_require_an_explicit_query(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    service = data_resource_services(providers)["knowledge-results"]

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(service.list_resources(_request_context(), PageQuery()))
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
