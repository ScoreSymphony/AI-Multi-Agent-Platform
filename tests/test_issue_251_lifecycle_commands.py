from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    DataProviderSet,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.domain import new_id


def _request_context() -> RequestContext:
    return RequestContext(
        request_id="request-251-command",
        correlation_id="correlation-251-command",
        actor=ActorContext(
            principal_ref="user:user-a",
            owner_type="user",
            owner_id="user-a",
        ),
        idempotency_key="idem-251-command",
    )


def _data_context() -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="correlation-251-command-data",
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


def test_memory_create_promote_update_delete_lifecycle(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handlers = data_command_handlers(providers)
    context = _request_context()

    short_term = asyncio.run(
        handlers["memory.create"](
            context,
            "session-251",
            {
                "scope": "short_term",
                "scope_id": "session-251",
                "origin": "agent-derived",
                "value": {"fact": "candidate"},
            },
        )
    )
    short_term_id = short_term["id"]
    assert isinstance(short_term_id, str)
    assert short_term["scope"] == "short_term"
    assert short_term["origin"] == "agent-derived"

    promoted = asyncio.run(
        handlers["memory.promote"](
            context,
            short_term_id,
            {"scope": "user", "scope_id": "user-a"},
        )
    )
    promoted_id = promoted["id"]
    assert isinstance(promoted_id, str)
    assert promoted["scope"] == "user"
    assert promoted["origin"] == "agent-derived"
    assert promoted["value"] == {"fact": "candidate"}
    assert any(
        item["kind"] == "memory" and item["ref"] == short_term_id for item in promoted["provenance"]
    )

    updated = asyncio.run(
        handlers["memory.update"](
            context,
            promoted_id,
            {"value": {"fact": "confirmed"}},
        )
    )
    updated_id = updated["id"]
    assert isinstance(updated_id, str)
    assert updated_id != promoted_id
    assert updated["supersedes_memory_id"] == promoted_id
    assert updated["value"] == {"fact": "confirmed"}
    assert updated["origin"] == "agent-derived"

    deleted = asyncio.run(handlers["memory.delete"](context, updated_id, {}))
    assert deleted == {"id": updated_id, "type": "memory", "deleted": True}


def test_memory_exact_expiry_requires_due_entry_and_exact_scope(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handlers = data_command_handlers(providers)
    context = _request_context()
    now = datetime.now(UTC)
    future = (now + timedelta(hours=1)).isoformat()

    due_entry = MemoryEntry(
        memory_id=new_memory_id(),
        scope=MemoryScope.USER,
        scope_id="user-a",
        owner_ref="user:user-a",
        created_by="user:user-a",
        value={"expires": "now"},
        created_at=now - timedelta(hours=1),
        retention=RetentionPolicy.USER_LIFETIME,
        expires_at=now - timedelta(minutes=1),
        origin=MemoryOrigin.AGENT_DERIVED,
    )
    asyncio.run(providers.memory.write_entry(due_entry, _data_context()))

    expired = asyncio.run(
        handlers["memory.expire"](
            context,
            due_entry.memory_id,
            {"scope": "user", "scope_id": "user-a"},
        )
    )
    assert expired == {"id": due_entry.memory_id, "type": "memory", "expired": True}

    not_due = asyncio.run(
        handlers["memory.create"](
            context,
            "user-a",
            {
                "scope": "user",
                "origin": "agent-derived",
                "value": {"expires": "later"},
                "expires_at": future,
            },
        )
    )
    not_due_id = not_due["id"]
    assert isinstance(not_due_id, str)
    with pytest.raises(ContractError) as future_exc:
        asyncio.run(
            handlers["memory.expire"](
                context,
                not_due_id,
                {"scope": "user", "scope_id": "user-a"},
            )
        )
    assert future_exc.value.code is ErrorCode.CONFLICT

    with pytest.raises(ContractError) as scope_exc:
        asyncio.run(
            handlers["memory.expire"](
                context,
                not_due_id,
                {"scope": "task", "scope_id": new_id("task")},
            )
        )
    assert scope_exc.value.code is ErrorCode.NOT_FOUND


def test_memory_update_cannot_reclassify_origin_or_scope(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handlers = data_command_handlers(providers)
    context = _request_context()
    created = asyncio.run(
        handlers["memory.create"](
            context,
            "user-a",
            {"scope": "user", "origin": "user-authored", "value": "stable"},
        )
    )
    memory_id = created["id"]
    assert isinstance(memory_id, str)

    with pytest.raises(ContractError) as origin_exc:
        asyncio.run(
            handlers["memory.update"](
                context,
                memory_id,
                {"origin": MemoryOrigin.IMPORTED.value},
            )
        )
    assert origin_exc.value.code is ErrorCode.INVALID_REQUEST

    with pytest.raises(ContractError) as scope_exc:
        asyncio.run(
            handlers["memory.update"](
                context,
                memory_id,
                {"scope": MemoryScope.AGENT.value},
            )
        )
    assert scope_exc.value.code is ErrorCode.INVALID_REQUEST


def test_memory_promote_rejects_non_short_term_source(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    handlers = data_command_handlers(providers)
    context = _request_context()
    created = asyncio.run(
        handlers["memory.create"](
            context,
            "user-a",
            {"scope": "user", "origin": "user-authored", "value": "already durable"},
        )
    )
    memory_id = created["id"]
    assert isinstance(memory_id, str)

    with pytest.raises(ContractError) as exc_info:
        asyncio.run(
            handlers["memory.promote"](
                context,
                memory_id,
                {"scope": "agent", "scope_id": new_id("agent")},
            )
        )
    assert exc_info.value.code is ErrorCode.INVALID_REQUEST


def test_knowledge_register_update_ingest_reindex_detach_lifecycle(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    project_id = new_id("project")
    handlers = data_command_handlers(providers, project_ids=lambda: (project_id,))
    context = _request_context()

    registered = asyncio.run(
        handlers["knowledge.register"](
            context,
            project_id,
            {"project_id": project_id, "title": "Issue 251 knowledge", "revision": "r1"},
        )
    )
    source_id = registered["id"]
    assert isinstance(source_id, str)
    assert registered["status"] == "registered"

    updated = asyncio.run(
        handlers["knowledge.update"](
            context,
            source_id,
            {"title": "Issue 251 knowledge updated", "metadata": {"kind": "reference"}},
        )
    )
    assert updated["id"] == source_id
    assert updated["title"] == "Issue 251 knowledge updated"
    assert updated["revision"] == "r1"
    assert updated["metadata"] == {"kind": "reference"}

    ingested = asyncio.run(
        handlers["knowledge.ingest"](
            context,
            source_id,
            {"content": "canonical source-backed knowledge", "location": "section:one"},
        )
    )
    assert ingested["source_id"] == source_id
    assert ingested["revision"] == "r1"

    reindexed = asyncio.run(
        handlers["knowledge.reindex"](
            context,
            source_id,
            {
                "revision": "r2",
                "content": "updated canonical knowledge",
                "location": "section:two",
            },
        )
    )
    assert reindexed["source_id"] == source_id
    assert reindexed["revision"] == "r2"

    detached = asyncio.run(handlers["knowledge.detach"](context, source_id, {}))
    assert detached["id"] == source_id
    assert detached["status"] == "removed"
    assert detached["detached"] is True


def test_knowledge_delete_preserves_canonical_source_tombstone(tmp_path: Path) -> None:
    providers = _providers(tmp_path)
    project_id = new_id("project")
    handlers = data_command_handlers(providers, project_ids=lambda: (project_id,))
    context = _request_context()
    registered = asyncio.run(
        handlers["knowledge.register"](
            context,
            project_id,
            {"project_id": project_id, "title": "Delete lifecycle", "revision": "r1"},
        )
    )
    source_id = registered["id"]
    assert isinstance(source_id, str)

    deleted = asyncio.run(handlers["knowledge.delete"](context, source_id, {}))
    assert deleted["id"] == source_id
    assert deleted["status"] == "removed"
    assert deleted["deleted"] is True
