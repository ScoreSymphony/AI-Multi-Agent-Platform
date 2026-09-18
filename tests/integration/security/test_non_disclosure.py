from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    OperationContext,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
    new_knowledge_source_id,
    new_memory_id,
)
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedDataKnowledgeProvider,
    AuthorizedDataMemoryProvider,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
)


def _context(*, owner_id: str = "alice", project_id: str | None = None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-251-nondisclosure",
            owner_type="user",
            owner_id=owner_id,
            project_id=project_id,
        ),
        actor_ref=f"user:{owner_id}",
    )


def _read_only_gate(project_id: str) -> AuthorizationGate:
    return AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:alice",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.READ}),
                    resource_types=frozenset({ResourceType.MEMORY, ResourceType.KNOWLEDGE_SOURCE}),
                    project_ids=frozenset({project_id}),
                ),
            )
        )
    )


def test_knowledge_list_is_authorized_before_source_count_or_metadata(tmp_path: Path) -> None:
    project_id = "project_00000000-0000-4000-8000-000000000251"
    raw = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_knowledge_source_id(),
        project_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        title="Hidden source title",
        revision="r1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    asyncio.run(raw.register_source(source, _context(project_id=project_id)))

    gate = _read_only_gate(project_id)
    protected = AuthorizedDataKnowledgeProvider(raw, gate)
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(protected.list_sources(_context(project_id=project_id)))

    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert "Hidden source title" not in str(exc_info.value)
    assert gate.audit_records[-1].action is AuthorizationAction.VIEW
    assert gate.audit_records[-1].outcome is AuthorizationOutcome.DENY


def test_knowledge_get_checks_authorization_before_provider_lookup(tmp_path: Path) -> None:
    project_id = "project_00000000-0000-4000-8000-000000000252"
    raw = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite3")
    gate = AuthorizationGate(
        LocalAuthorizationProvider(
            (
                LocalPrincipalPolicy(
                    principal_ref="user:alice",
                    actor_types=frozenset({ActorType.HUMAN}),
                    allowed_actions=frozenset({AuthorizationAction.CREATE}),
                    resource_types=frozenset({ResourceType.KNOWLEDGE_SOURCE}),
                    project_ids=frozenset({project_id}),
                ),
            )
        )
    )
    protected = AuthorizedDataKnowledgeProvider(raw, gate)
    source_id = new_knowledge_source_id()

    with pytest.raises(ContractError) as lookup_error:
        asyncio.run(protected.get_source(source_id, _context(project_id=project_id)))
    assert lookup_error.value.code is ErrorCode.FORBIDDEN

    assert gate.audit_records[-1].action is AuthorizationAction.READ
    assert gate.audit_records[-1].resource_id == source_id
    assert gate.audit_records[-1].outcome is AuthorizationOutcome.DENY


def test_memory_write_policy_receives_origin_without_leaking_backend_details(
    tmp_path: Path,
) -> None:
    project_id = "project_00000000-0000-4000-8000-000000000253"
    raw = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    gate = _read_only_gate(project_id)
    protected = AuthorizedDataMemoryProvider(raw, gate)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.WORKSPACE,
        scope_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        value={"private": "memory-content"},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.PROJECT_LIFETIME,
        origin=MemoryOrigin.IMPORTED,
    )

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(protected.write_entry(entry, _context(project_id=project_id)))
    assert exc_info.value.code is ErrorCode.FORBIDDEN
    assert (
        asyncio.run(
            raw.query_entries(
                MemoryQuery(MemoryScope.WORKSPACE, project_id),
                _context(project_id=project_id),
            )
        )
        == ()
    )

    action = gate.audit_records[-1]
    assert action.action is AuthorizationAction.CREATE
    assert action.outcome is AuthorizationOutcome.DENY


def test_memory_expiry_is_authorized_before_due_entry_is_tombstoned(tmp_path: Path) -> None:
    project_id = "project_00000000-0000-4000-8000-000000000254"
    raw = LocalMemoryProvider(tmp_path / "memory.sqlite3")
    context = _context(project_id=project_id)
    entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.WORKSPACE,
        scope_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        value={"private": "due-memory"},
        created_at=datetime.now(UTC) - timedelta(hours=1),
        retention=RetentionPolicy.PROJECT_LIFETIME,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        origin=MemoryOrigin.AGENT_DERIVED,
    )
    asyncio.run(raw.write_entry(entry, context))

    gate = _read_only_gate(project_id)
    protected = AuthorizedDataMemoryProvider(raw, gate)
    query = MemoryQuery(
        MemoryScope.WORKSPACE,
        project_id,
        include_expired=True,
        include_superseded=True,
    )
    with pytest.raises(ContractError) as exc_info:
        asyncio.run(protected.expire_entry(entry.memory_id, query, context))
    assert exc_info.value.code is ErrorCode.FORBIDDEN

    visible_to_raw_provider = asyncio.run(raw.query_entries(query, context))
    assert [candidate.memory_id for candidate in visible_to_raw_provider] == [entry.memory_id]
    action = gate.audit_records[-1]
    assert action.action is AuthorizationAction.MODIFY
    assert action.resource_id == entry.memory_id
    assert action.outcome is AuthorizationOutcome.DENY
