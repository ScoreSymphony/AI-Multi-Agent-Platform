from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from data_lifecycle_command_cases import _data_context, _providers, _request_context

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.data import (
    MemoryEntry,
    MemoryOrigin,
    MemoryScope,
    RetentionPolicy,
    new_memory_id,
)
from ai_multi_agent_platform.data.lifecycle_commands import data_command_handlers
from ai_multi_agent_platform.domain import new_id


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
