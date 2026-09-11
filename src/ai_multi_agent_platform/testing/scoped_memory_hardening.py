"""Issue-#745 hardening for refined scoped-memory provider conformance."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.data import (
    DataAccessContext,
    MemoryEntry,
    MemoryOrigin,
    MemoryProvider,
    MemoryQuery,
    MemoryScope,
    MemoryType,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id

from .scoped_memory import (
    assert_scoped_memory_provider_contract as _assert_base_scoped_memory_provider_contract,
)

_FILTER_MARKER = "issue-745-filter-marker"


def _filter_entry(
    *,
    scope_id: str,
    owner_ref: str,
    memory_type: MemoryType,
    created_at: datetime,
    label: str,
) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.TASK,
        scope_id=scope_id,
        owner_ref=owner_ref,
        created_by=owner_ref,
        value={"text": f"{_FILTER_MARKER} {label}"},
        created_at=created_at,
        retention=RetentionPolicy.TASK_LIFETIME,
        origin=MemoryOrigin.IMPORTED,
        provenance=(SourceRef(kind="conformance", ref=f"issue-745:{label}"),),
        memory_type=memory_type,
    )


async def _assert_query_filter_composition(
    provider: MemoryProvider,
    context: DataAccessContext,
) -> None:
    """Verify scope/owner/type visibility is composed before the canonical limit."""

    scope_id = new_id("task")
    foreign_scope_id = new_id("task")
    owner_ref = context.actor_ref
    foreign_owner_ref = "user:memory-conformance-other"
    now = datetime.now(UTC)

    wrong_type = _filter_entry(
        scope_id=scope_id,
        owner_ref=owner_ref,
        memory_type=MemoryType.PREFERENCE,
        created_at=now,
        label="wrong-type",
    )
    wrong_owner = _filter_entry(
        scope_id=scope_id,
        owner_ref=foreign_owner_ref,
        memory_type=MemoryType.SEMANTIC,
        created_at=now - timedelta(seconds=1),
        label="wrong-owner",
    )
    wrong_scope = _filter_entry(
        scope_id=foreign_scope_id,
        owner_ref=owner_ref,
        memory_type=MemoryType.SEMANTIC,
        created_at=now - timedelta(seconds=2),
        label="wrong-scope",
    )
    target = _filter_entry(
        scope_id=scope_id,
        owner_ref=owner_ref,
        memory_type=MemoryType.SEMANTIC,
        created_at=now - timedelta(seconds=3),
        label="target",
    )

    # Deliberately write decoys before the target. A provider that applies limit before
    # canonical filters will consume one of the decoys and lose the valid result.
    for entry in (wrong_type, wrong_owner, wrong_scope, target):
        await provider.write_entry(entry, context)

    owner_visible = await provider.query_entries(
        MemoryQuery(
            MemoryScope.TASK,
            scope_id,
            owner_ref=owner_ref,
            memory_types=(),
            limit=10,
        ),
        context,
    )
    if {entry.memory_id for entry in owner_visible} != {
        wrong_type.memory_id,
        target.memory_id,
    }:
        raise AssertionError(
            "memory query must compose scope and owner_ref independently from memory_type"
        )

    filtered_query = MemoryQuery(
        MemoryScope.TASK,
        scope_id,
        owner_ref=owner_ref,
        memory_types=(MemoryType.SEMANTIC,),
        limit=1,
    )
    filtered = await provider.query_entries(filtered_query, context)
    if tuple(entry.memory_id for entry in filtered) != (target.memory_id,):
        raise AssertionError(
            "memory query must apply scope, owner_ref and memory_types before limit"
        )

    search_hits = await provider.search_entries(
        filtered_query,
        _FILTER_MARKER,
        context,
    )
    if tuple(entry.memory_id for entry in search_hits) != (target.memory_id,):
        raise AssertionError(
            "memory search must preserve canonical scope/owner/type filters and limit"
        )


async def assert_scoped_memory_provider_contract(
    provider: MemoryProvider,
    context: DataAccessContext,
) -> None:
    """Verify the complete refined scoped-Memory provider contract.

    The original reusable suite introduced by #746 proves canonical ``MemoryType``
    round-tripping, exact/multi-type filtering, search parity and lifecycle semantics.
    Issue #745 additionally requires filter composition: scope and ownership remain
    independent authorization/visibility dimensions, and the result limit is applied only
    after canonical scope/owner/type filters.
    """

    await _assert_base_scoped_memory_provider_contract(provider, context)
    await _assert_query_filter_composition(provider, context)
