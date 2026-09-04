from __future__ import annotations

from datetime import UTC, datetime

from ai_multi_agent_platform.data import (
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


def _entry(*, scope: MemoryScope, scope_id: str, origin: MemoryOrigin) -> MemoryEntry:
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"fact": "canonical"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        origin=origin,
    )


def test_memory_origin_is_explicit_canonical_semantics() -> None:
    for origin in MemoryOrigin:
        entry = _entry(
            scope=MemoryScope.AGENT,
            scope_id=new_id("agent"),
            origin=origin,
        )
        assert entry.origin is origin
        assert entry.provenance


def test_organization_memory_scope_is_distinct_and_policy_controlled() -> None:
    entry = _entry(
        scope=MemoryScope.ORGANIZATION,
        scope_id="org:example",
        origin=MemoryOrigin.IMPORTED,
    )

    assert entry.scope is MemoryScope.ORGANIZATION
    assert entry.scope_id == "org:example"
    assert "authorized_organization_member" in entry.access_policy.readers
    assert entry.access_policy.cross_project_access == "organization_policy_controlled"


def test_project_memory_continues_to_use_workspace_scope() -> None:
    project_id = new_id("project")
    entry = _entry(
        scope=MemoryScope.WORKSPACE,
        scope_id=project_id,
        origin=MemoryOrigin.AGENT_DERIVED,
    )

    assert entry.scope is MemoryScope.WORKSPACE
    assert entry.scope_id == project_id
    assert entry.access_policy.cross_project_access == "deny"


def test_default_memory_origin_is_user_authored_for_backward_compatibility() -> None:
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="pre-251 compatible",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.USER_LIFETIME,
    )

    assert entry.origin is MemoryOrigin.USER_AUTHORED
