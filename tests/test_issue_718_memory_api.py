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
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.control_plane import data_resource_services
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-718",
        correlation_id="correlation-718",
        actor=ActorContext(
            principal_ref="user:user-a",
            owner_type="user",
            owner_id="user-a",
        ),
    )


def _data_context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="correlation-718",
            owner_type="user",
            owner_id="user-a",
        ),
        actor_ref="user:user-a",
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _memory(memory_type: MemoryType, value: str) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value=value,
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        memory_type=memory_type,
    )


def test_memory_read_api_projects_and_filters_canonical_memory_type(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    context = _data_context()
    semantic = _memory(MemoryType.SEMANTIC, "stable architecture fact")
    procedural = _memory(MemoryType.PROCEDURAL, "validated review workflow")
    asyncio.run(providers.memory.write_entry(semantic, context))
    asyncio.run(providers.memory.write_entry(procedural, context))

    service = data_resource_services(providers)["memory"]
    resources = asyncio.run(
        service.list_resources(
            _request_context(),
            PageQuery(filters={"memory_type": "procedural"}),
        )
    )

    assert len(resources) == 1
    assert resources[0]["id"] == procedural.memory_id
    assert resources[0]["memory_type"] == "procedural"


def test_memory_read_api_rejects_unknown_memory_type_filter(tmp_path: Path) -> None:
    service = data_resource_services(_providers(tmp_path))["memory"]

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            service.list_resources(
                _request_context(),
                PageQuery(filters={"memory_type": "provider-private"}),
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_memory_create_command_accepts_and_returns_canonical_memory_type(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handler = data_command_handlers(providers)["memory.create"]

    resource = asyncio.run(
        handler(
            _request_context(),
            "user-a",
            {
                "scope": "user",
                "scope_id": "user-a",
                "origin": "user-authored",
                "memory_type": "preference",
                "value": {"response_style": "compact"},
            },
        )
    )

    assert resource["memory_type"] == "preference"
    stored = asyncio.run(providers.memory.get_entry(str(resource["id"]), _data_context()))
    assert stored.memory_type is MemoryType.PREFERENCE


def test_memory_create_command_defaults_legacy_callers_to_unclassified(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handler = data_command_handlers(providers)["memory.create"]

    resource = asyncio.run(
        handler(
            _request_context(),
            "user-a",
            {
                "scope": "user",
                "scope_id": "user-a",
                "origin": "imported",
                "value": "legacy-compatible",
            },
        )
    )

    assert resource["memory_type"] == "unclassified"


def test_memory_update_cannot_silently_change_semantic_type(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    current = _memory(MemoryType.EPISODIC, "observed failure")
    asyncio.run(providers.memory.write_entry(current, _data_context()))
    handler = data_command_handlers(providers)["memory.update"]

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            handler(
                _request_context(),
                current.memory_id,
                {"memory_type": "reflective", "value": "derived lesson"},
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_global_search_projection_exposes_type_without_memory_value(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    entry = _memory(MemoryType.REFLECTIVE, "private derived lesson")
    asyncio.run(providers.memory.write_entry(entry, _data_context()))

    service = data_resource_services(providers)["memory"]
    resources = asyncio.run(service.list_search_resources())

    assert len(resources) == 1
    assert resources[0]["memory_type"] == "reflective"
    assert "reflective" in resources[0]["aliases"]
    assert "value" not in resources[0]
