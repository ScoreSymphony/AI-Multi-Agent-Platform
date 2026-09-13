from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.reference import LocalMemoryProvider as LegacyLocalMemoryProvider


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-718",
            owner_type="user",
            owner_id="user-a",
        ),
        actor_ref="user:user-a",
    )


def _entry(memory_type: MemoryType, *, value: str | None = None) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value=value or memory_type.value,
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        memory_type=memory_type,
    )


def test_memory_type_taxonomy_is_provider_neutral_and_scope_independent() -> None:
    assert tuple(memory_type.value for memory_type in MemoryType) == (
        "unclassified",
        "episodic",
        "semantic",
        "procedural",
        "preference",
        "reflective",
    )

    for memory_type in MemoryType:
        entry = _entry(memory_type)
        assert entry.scope is MemoryScope.USER
        assert entry.memory_type is memory_type


def test_memory_entry_normalizes_strings_and_rejects_unknown_types() -> None:
    semantic = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="fact",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
        memory_type="semantic",  # type: ignore[arg-type]
    )
    assert semantic.memory_type is MemoryType.SEMANTIC

    with pytest.raises(ValueError, match="unknown memory type"):
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="user-a",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="fact",
            created_at=datetime.now(UTC),
            retention=RetentionPolicy.USER_LIFETIME,
            memory_type="provider-private-tag",  # type: ignore[arg-type]
        )


def test_unclassified_is_deterministic_backward_compatibility_default() -> None:
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="legacy",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
    )
    assert entry.memory_type is MemoryType.UNCLASSIFIED


def test_local_provider_round_trips_and_filters_memory_types(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "data.sqlite")
    context = _context()
    entries = (
        _entry(MemoryType.SEMANTIC, value="architecture canonical"),
        _entry(MemoryType.PROCEDURAL, value="review canonical workflow"),
        _entry(MemoryType.EPISODIC, value="canonical tool observation"),
    )
    for entry in entries:
        asyncio.run(provider.write_entry(entry, context))
        assert (
            asyncio.run(provider.get_entry(entry.memory_id, context)).memory_type
            is entry.memory_type
        )

    filtered = asyncio.run(
        provider.query_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                memory_types=(MemoryType.SEMANTIC, MemoryType.PROCEDURAL),
            ),
            context,
        )
    )
    assert {entry.memory_type for entry in filtered} == {
        MemoryType.SEMANTIC,
        MemoryType.PROCEDURAL,
    }

    searched = asyncio.run(
        provider.search_entries(
            MemoryQuery(
                MemoryScope.USER,
                "user-a",
                memory_types=(MemoryType.EPISODIC,),
            ),
            "canonical",
            context,
        )
    )
    assert len(searched) == 1
    assert searched[0].memory_type is MemoryType.EPISODIC

    features = provider.descriptor.capabilities[0].features
    assert "memory_type_taxonomy" in features
    assert "memory_type_filtering" in features


def test_local_provider_preserves_memory_type_across_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite"
    context = _context()
    entry = _entry(MemoryType.PREFERENCE)
    asyncio.run(LocalMemoryProvider(db_path).write_entry(entry, context))

    restarted = LocalMemoryProvider(db_path)
    restored = asyncio.run(restarted.get_entry(entry.memory_id, context))
    assert restored.memory_type is MemoryType.PREFERENCE


def test_local_provider_migrates_pre_718_rows_to_unclassified(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    context = _context()
    legacy_entry = _entry(MemoryType.UNCLASSIFIED, value="pre-718")

    legacy = LegacyLocalMemoryProvider(db_path)
    asyncio.run(legacy.write_entry(legacy_entry, context))

    migrated = LocalMemoryProvider(db_path)
    restored = asyncio.run(migrated.get_entry(legacy_entry.memory_id, context))
    assert restored.memory_type is MemoryType.UNCLASSIFIED

    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(data_memory)")}
        stored_type = connection.execute(
            "SELECT memory_type FROM data_memory WHERE memory_id = ?",
            (legacy_entry.memory_id,),
        ).fetchone()
    assert "memory_type" in columns
    assert stored_type == (MemoryType.UNCLASSIFIED.value,)


def test_invalid_stored_memory_type_fails_canonically(tmp_path: Path) -> None:
    db_path = tmp_path / "data.sqlite"
    provider = LocalMemoryProvider(db_path)
    context = _context()
    entry = _entry(MemoryType.REFLECTIVE)
    asyncio.run(provider.write_entry(entry, context))

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE data_memory SET memory_type = ? WHERE memory_id = ?",
            ("backend-secret-class", entry.memory_id),
        )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(entry.memory_id, context))
    assert exc_info.value.code is ErrorCode.CONTRACT_VIOLATION
