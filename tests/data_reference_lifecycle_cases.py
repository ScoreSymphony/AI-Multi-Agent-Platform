from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)


def _context(
    *,
    owner_type: str = "user",
    owner_id: str = "user-a",
    project_id: str | None = None,
) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-251-reference",
            owner_type=owner_type,
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref=f"{owner_type}:{owner_id}",
    )


def _memory_entry(
    *,
    scope: MemoryScope,
    scope_id: str,
    owner_ref: str,
    origin: MemoryOrigin,
) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref=owner_ref,
        created_by=owner_ref,
        value={"fact": "persisted", "origin": origin.value},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        origin=origin,
    )
