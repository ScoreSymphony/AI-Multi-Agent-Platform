from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ai_multi_agent_platform.data import (
    MemoryEntry,
    MemoryScope,
    RetentionPolicy,
    SourceRef,
    new_memory_id,
)
from ai_multi_agent_platform.domain import new_id


def _durable_entry(scope: MemoryScope, scope_id: str) -> MemoryEntry:
    retention = {
        MemoryScope.TASK: RetentionPolicy.TASK_LIFETIME,
        MemoryScope.AGENT: RetentionPolicy.DURABLE,
        MemoryScope.WORKSPACE: RetentionPolicy.PROJECT_LIFETIME,
        MemoryScope.USER: RetentionPolicy.USER_LIFETIME,
    }[scope]
    return MemoryEntry(
        memory_id=new_memory_id(),
        scope=scope,
        scope_id=scope_id,
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"scope": scope.value},
        created_at=datetime.now(UTC),
        retention=retention,
    )


def test_every_durable_memory_scope_has_provenance_semantics() -> None:
    project_id = new_id("project")
    cases = (
        (MemoryScope.TASK, new_id("task")),
        (MemoryScope.AGENT, new_id("agent")),
        (MemoryScope.WORKSPACE, project_id),
        (MemoryScope.USER, "user-a"),
    )

    for scope, scope_id in cases:
        entry = _durable_entry(scope, scope_id)
        assert entry.provenance == (SourceRef(kind="memory_writer", ref="user:user-a"),)

    historical = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.HISTORICAL,
        scope_id="history:task",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value="summary",
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.DURABLE,
        provenance=(SourceRef(kind="event", ref="event-canonical-evidence"),),
    )
    assert historical.provenance[0].kind == "event"


def test_memory_access_semantics_are_explicit_for_all_six_scopes() -> None:
    project_id = new_id("project")
    now = datetime.now(UTC)
    entries = (
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.SHORT_TERM,
            scope_id="execution-1",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="context",
            created_at=now,
            retention=RetentionPolicy.EPHEMERAL,
            expires_at=now + timedelta(minutes=15),
        ),
        _durable_entry(MemoryScope.TASK, new_id("task")),
        _durable_entry(MemoryScope.AGENT, new_id("agent")),
        _durable_entry(MemoryScope.WORKSPACE, project_id),
        _durable_entry(MemoryScope.USER, "user-a"),
        MemoryEntry(
            memory_id=new_memory_id(),
            scope=MemoryScope.HISTORICAL,
            scope_id="history:project",
            owner_ref="user:user-a",
            created_by="user:user-a",
            value="history",
            created_at=now,
            retention=RetentionPolicy.DURABLE,
            provenance=(SourceRef(kind="event", ref="event-1"),),
        ),
    )

    for entry in entries:
        policy = entry.access_policy
        assert policy.readers
        assert policy.writers
        assert policy.agent_revision_access
        assert policy.team_access
        assert policy.task_inheritance
        assert policy.cross_project_access

    assert entries[0].execution_ref == "execution-1"
    assert entries[0].access_policy.agent_revision_access == "context_bound"
    assert entries[2].access_policy.agent_revision_access == "same_agent_policy_controlled"
    assert entries[2].access_policy.team_access == "explicit_policy_only"
    assert entries[3].access_policy.task_inheritance == "explicit_only"
    assert entries[4].access_policy.cross_project_access == "explicit_policy_only"
