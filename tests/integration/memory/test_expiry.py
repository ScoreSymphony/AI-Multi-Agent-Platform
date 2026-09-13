from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.contracts.types import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.control_plane import MemoryResourceService


def _context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-290-expiry",
            owner_type="user",
            owner_id="alice",
        ),
        actor_ref="user:alice",
    )


def test_expired_memory_is_absent_from_rebuild_discovery_snapshot(tmp_path) -> None:
    async def scenario() -> None:
        provider = LocalMemoryProvider(tmp_path / "memory.sqlite3")
        now = datetime.now(UTC)
        expired = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            created_by="user:alice",
            value={"text": "expired-private-value"},
            created_at=now - timedelta(minutes=10),
            retention=RetentionPolicy.USER_LIFETIME,
            expires_at=now - timedelta(minutes=1),
            origin=MemoryOrigin.AGENT_DERIVED,
        )
        live = MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.USER,
            scope_id="alice",
            owner_ref="user:alice",
            created_by="user:alice",
            value={"text": "live-private-value"},
            created_at=now,
            retention=RetentionPolicy.USER_LIFETIME,
            expires_at=now + timedelta(minutes=10),
            origin=MemoryOrigin.USER_AUTHORED,
        )
        await provider.write_entry(expired, _context())
        await provider.write_entry(live, _context())

        resources = await MemoryResourceService(provider).list_search_resources()
        resource_ids = {resource["id"] for resource in resources}
        assert expired.memory_id not in resource_ids
        assert live.memory_id in resource_ids
        assert all("value" not in resource for resource in resources)

    asyncio.run(scenario())
