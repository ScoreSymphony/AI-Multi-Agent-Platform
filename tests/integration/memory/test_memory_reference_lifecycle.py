from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from data_reference_lifecycle_cases import _context, _memory_entry

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import (
    LocalMemoryProvider,
    MemoryOrigin,
    MemoryScope,
)
from ai_multi_agent_platform.data.reference import LocalMemoryProvider as PreLifecycleMemoryProvider


def test_memory_origin_migrates_existing_database_and_survives_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    context = _context()

    pre_lifecycle = PreLifecycleMemoryProvider(db_path)
    old_entry = _memory_entry(
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        origin=MemoryOrigin.USER_AUTHORED,
    )
    asyncio.run(pre_lifecycle.write_entry(old_entry, context))

    migrated = LocalMemoryProvider(db_path)
    assert (
        asyncio.run(migrated.get_entry(old_entry.memory_id, context)).origin
        is MemoryOrigin.USER_AUTHORED
    )

    imported = _memory_entry(
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        origin=MemoryOrigin.IMPORTED,
    )
    asyncio.run(migrated.write_entry(imported, context))

    restarted = LocalMemoryProvider(db_path)
    restored = asyncio.run(restarted.get_entry(imported.memory_id, context))
    assert restored.origin is MemoryOrigin.IMPORTED
    assert "seven_scopes" in restarted.descriptor.capabilities[0].features
    assert "memory_origin" in restarted.descriptor.capabilities[0].features
    assert "six_scopes" not in restarted.descriptor.capabilities[0].features


def test_organization_memory_rejects_mismatched_organization_owner(tmp_path: Path) -> None:
    provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    organization_id = "org:alpha"
    entry = _memory_entry(
        scope=MemoryScope.ORGANIZATION,
        scope_id=organization_id,
        owner_ref=f"organization:{organization_id}",
        origin=MemoryOrigin.IMPORTED,
    )
    allowed = _context(owner_type="organization", owner_id=organization_id)
    asyncio.run(provider.write_entry(entry, allowed))

    denied = _context(owner_type="organization", owner_id="org:beta")
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(provider.get_entry(entry.memory_id, denied))
    assert exc_info.value.code is ErrorCode.FORBIDDEN
