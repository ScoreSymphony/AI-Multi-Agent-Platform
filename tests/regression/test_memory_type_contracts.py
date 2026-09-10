from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers

_SCOPE_IDS = {
    MemoryScope.SHORT_TERM: "session-memory-type",
    MemoryScope.TASK: "task_00000000-0000-0000-0000-000000000001",
    MemoryScope.AGENT: "agent_00000000-0000-0000-0000-000000000002",
    MemoryScope.WORKSPACE: "project_00000000-0000-0000-0000-000000000003",
    MemoryScope.USER: "user-a",
    MemoryScope.HISTORICAL: "history-memory-type",
    MemoryScope.ORGANIZATION: "organization-a",
}


def _context(*, owner_id: str = "user-a") -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-memory-type",
            owner_type="user",
            owner_id=owner_id,
        ),
        actor_ref=f"user:{owner_id}",
    )


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-memory-type",
        correlation_id="corr-memory-type",
        actor=ActorContext(
            principal_ref="user:user-a",
            owner_type="user",
            owner_id="user-a",
        ),
    )


def _providers(tmp_path: Path) -> DataProviderSet:
    return DataProviderSet(
        files=LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3"),
        memory=LocalMemoryProvider(tmp_path / "memory.sqlite3"),
        knowledge=LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3"),
    )


def _entry(
    memory_type: MemoryType,
    *,
    scope: MemoryScope = MemoryScope.USER,
    value: str | None = None,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    retention: RetentionPolicy | None = None,
    provenance: tuple[SourceRef, ...] = (),
) -> MemoryEntry:
    created = created_at or datetime.now(UTC)
    selected_retention = retention or RetentionPolicy.DURABLE
    selected_provenance = provenance
    if scope is MemoryScope.SHORT_TERM:
        selected_retention = retention or RetentionPolicy.EPHEMERAL
        expires_at = expires_at or created + timedelta(hours=1)
    elif scope is MemoryScope.HISTORICAL and not selected_provenance:
        selected_provenance = (SourceRef(kind="test", ref="historical-source"),)
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=_SCOPE_IDS[scope],
        owner_ref="user:user-a",
        created_by="user:user-a",
        value=value or memory_type.value,
        created_at=created,
        retention=selected_retention,
        expires_at=expires_at,
        provenance=selected_provenance,
        memory_type=memory_type,
    )


@pytest.mark.parametrize("scope", tuple(MemoryScope))
@pytest.mark.parametrize("memory_type", tuple(MemoryType))
def test_every_memory_type_constructs_in_every_valid_scope(
    scope: MemoryScope,
    memory_type: MemoryType,
) -> None:
    entry = _entry(memory_type, scope=scope)

    assert entry.scope is scope
    assert entry.memory_type is memory_type


def test_memory_type_is_independent_from_other_memory_dimensions() -> None:
    provenance = (SourceRef(kind="evidence", ref="source-memory-type"),)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.AGENT,
        scope_id=_SCOPE_IDS[MemoryScope.AGENT],
        owner_ref="user:user-a",
        created_by="agent:memory-curator",
        value="derived conclusion",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        origin=MemoryOrigin.IMPORTED,
        provenance=provenance,
        classification=DataClassification.CONFIDENTIAL,
        memory_type=MemoryType.REFLECTIVE,
    )

    assert entry.memory_type is MemoryType.REFLECTIVE
    assert entry.origin is MemoryOrigin.IMPORTED
    assert entry.retention is RetentionPolicy.DURABLE
    assert entry.provenance == provenance
    assert entry.classification is DataClassification.CONFIDENTIAL


def test_local_provider_round_trips_every_type_and_keeps_scope_only_queries(
    tmp_path: Path,
) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    context = _context()
    entries = tuple(_entry(memory_type) for memory_type in MemoryType)
    for entry in entries:
        asyncio.run(provider.write_entry(entry, context))
        restored = asyncio.run(provider.get_entry(entry.memory_id, context))
        assert restored.memory_type is entry.memory_type

    scope_only = asyncio.run(
        provider.query_entries(MemoryQuery(MemoryScope.USER, "user-a"), context)
    )
    assert {entry.memory_type for entry in scope_only} == set(MemoryType)

    exact = asyncio.run(
        provider.query_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                memory_types=(MemoryType.PREFERENCE,),
            ),
            context,
        )
    )
    assert [entry.memory_type for entry in exact] == [MemoryType.PREFERENCE]

    multi = asyncio.run(
        provider.query_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                memory_types=(MemoryType.SEMANTIC, MemoryType.REFLECTIVE),
            ),
            context,
        )
    )
    assert {entry.memory_type for entry in multi} == {
        MemoryType.SEMANTIC,
        MemoryType.REFLECTIVE,
    }


def test_supersession_preserves_source_type_and_uses_replacement_type(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    context = _context()
    original = _entry(MemoryType.EPISODIC, value="observed outcome")
    asyncio.run(provider.write_entry(original, context))
    replacement = _entry(
        MemoryType.REFLECTIVE,
        value="derived lesson",
        provenance=(SourceRef(kind="memory", ref=original.memory_id),),
    )

    linked = asyncio.run(provider.supersede_entry(original.memory_id, replacement, context))

    assert linked.memory_type is MemoryType.REFLECTIVE
    assert linked.supersedes_memory_id == original.memory_id
    assert linked.provenance == (SourceRef(kind="memory", ref=original.memory_id),)
    visible = asyncio.run(provider.query_entries(MemoryQuery(MemoryScope.USER, "user-a"), context))
    assert [entry.memory_id for entry in visible] == [linked.memory_id]
    complete = asyncio.run(
        provider.query_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                include_superseded=True,
                memory_types=(MemoryType.EPISODIC, MemoryType.REFLECTIVE),
            ),
            context,
        )
    )
    assert {entry.memory_type for entry in complete} == {
        MemoryType.EPISODIC,
        MemoryType.REFLECTIVE,
    }


def test_expiry_and_delete_keep_existing_lifecycle_semantics(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    context = _context()
    now = datetime.now(UTC)
    due = _entry(
        MemoryType.PREFERENCE,
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
        retention=RetentionPolicy.UNTIL,
    )
    removable = _entry(MemoryType.PROCEDURAL)
    asyncio.run(provider.write_entry(due, context))
    asyncio.run(provider.write_entry(removable, context))

    expired = asyncio.run(
        provider.expire_entry(
            due.memory_id,
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                include_expired=True,
                memory_types=(MemoryType.PREFERENCE,),
            ),
            context,
        )
    )
    assert expired.memory_type is MemoryType.PREFERENCE
    asyncio.run(provider.delete_entry(removable.memory_id, context))

    for memory_id in (due.memory_id, removable.memory_id):
        with pytest.raises(ContractError) as exc_info:
            asyncio.run(provider.get_entry(memory_id, context))
        assert exc_info.value.code is ErrorCode.NOT_FOUND


def test_type_filter_cannot_bypass_scope_authorization(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    entry = _entry(MemoryType.SEMANTIC)
    asyncio.run(provider.write_entry(entry, _context()))

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            provider.query_entries(
                MemoryQuery(
                    MemoryScope.USER,
                    "user-a",
                    memory_types=(MemoryType.SEMANTIC,),
                ),
                _context(owner_id="user-b"),
            )
        )
    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_promotion_preserves_type_and_rejects_reclassification(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handlers = data_command_handlers(providers)
    created = asyncio.run(
        handlers["memory.create"](
            _request_context(),
            "session-memory-type",
            {
                "scope": "short_term",
                "scope_id": "session-memory-type",
                "origin": "agent-derived",
                "memory_type": "episodic",
                "value": "observed outcome",
            },
        )
    )
    memory_id = str(created["id"])

    promoted = asyncio.run(
        handlers["memory.promote"](
            _request_context(),
            memory_id,
            {"scope": "user", "scope_id": "user-a"},
        )
    )
    assert promoted["memory_type"] == "episodic"

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            handlers["memory.promote"](
                _request_context(),
                memory_id,
                {
                    "scope": "user",
                    "scope_id": "user-a",
                    "memory_type": "reflective",
                },
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_create_api_rejects_provider_private_memory_type(tmp_path: Path) -> None:
    handler = data_command_handlers(_providers(tmp_path))["memory.create"]

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            handler(
                _request_context(),
                "user-a",
                {
                    "scope": "user",
                    "scope_id": "user-a",
                    "origin": "user-authored",
                    "memory_type": "provider-private",
                    "value": "invalid",
                },
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST
